# core/collector.py
# Read-only collector v0.8
# - account_snapshot
# - positions_snapshot
# - ticker_snapshot
# - reconcile missing positions -> synthetic contracts=0 row
# - watches symbols from:
#     a) live positions
#     b) pending_limit_tasks with status in ('PENDING', 'TRIGGERED') [legacy, optional]
#     c) pending_chase_tasks with status in ('PENDING') [legacy, optional]
# - can mark pending limit tasks when price crosses limit [legacy, optional]
# - can create/realize lots from pending limit fills [legacy, optional]
# - pending_chase_tasks block is legacy-only and disabled by default
# - REMOVED: fills/order-history API polling, because MEXC returns 700007

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import ccxt
from dotenv import load_dotenv

from core.db import connect
from core.lots import create_lot_from_open_fill, close_qty_against_lots
from core.symbol_utils import canonical_futures_symbol

LEGACY_PENDING_LIMIT_TRACKING_ENABLED = False
LEGACY_PENDING_CHASE_TRACKING_ENABLED = False

PENDING_EXPIRE_MINUTES = 180
TRIGGERED_FAIL_MINUTES = 30
CHASE_EXPIRE_MINUTES = 30

DEFAULT_LOT_TARGET_ROI_PCT = 200.0
DEFAULT_LOT_LEVERAGE = 500.0

load_dotenv()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(x: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def base_symbol(sym: Optional[str]) -> Optional[str]:
    if not sym:
        return sym
    return sym.split(":")[0]


def make_exchange() -> ccxt.Exchange:
    api_key = os.getenv("MEXC_API_KEY")
    api_secret = os.getenv("MEXC_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("Missing MEXC_API_KEY / MEXC_API_SECRET in environment (.env).")

    return ccxt.mexc(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        }
    )


def normalize_ccxt_positions(raw_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in raw_positions:
        sym = base_symbol(p.get("symbol"))

        side_raw = p.get("side")
        side = None
        if str(side_raw).lower() == "long":
            side = "LONG"
        elif str(side_raw).lower() == "short":
            side = "SHORT"

        contracts = safe_float(p.get("contracts"), 0.0) or 0.0
        entry = safe_float(p.get("entryPrice"), 0.0) or 0.0
        upnl = safe_float(p.get("unrealizedPnl"), 0.0) or 0.0

        if not sym or not side:
            continue

        if contracts == 0.0:
            continue

        out.append(
            {
                "symbol": sym,
                "side": side,
                "contracts": contracts,
                "entryPrice": entry,
                "unrealizedPnl": upnl,
            }
        )
    return out


def _pos_key(symbol: Optional[str], side: Optional[str]) -> Tuple[str, str]:
    return (symbol or "", side or "")


def get_latest_open_positions_from_db(con) -> Dict[Tuple[str, str], Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT ps.symbol, ps.side, ps.contracts, ps.entry_price
        FROM positions_snapshot ps
        JOIN (
            SELECT symbol, side, MAX(created_at) AS max_created_at
            FROM positions_snapshot
            GROUP BY symbol, side
        ) last
        ON ps.symbol = last.symbol
        AND ps.side = last.side
        AND ps.created_at = last.max_created_at
        WHERE ps.contracts > 0
        """
    ).fetchall()

    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for sym, side, contracts, entry_price in rows:
        out[_pos_key(sym, side)] = {
            "symbol": sym,
            "side": side,
            "contracts": float(contracts) if contracts is not None else 0.0,
            "entryPrice": float(entry_price) if entry_price is not None else 0.0,
        }
    return out


def insert_position_snapshot(
    con,
    snapshot_ts: str,
    symbol: str,
    side: str,
    contracts: float,
    entry_price: float,
    unrealized_pnl: float,
) -> None:
    con.execute(
        """
        INSERT INTO positions_snapshot
        (created_at, symbol, side, contracts, entry_price, unrealized_pnl)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_ts,
            symbol,
            side,
            contracts,
            entry_price,
            unrealized_pnl,
        ),
    )


def insert_ticker_snapshot(
    con,
    snapshot_ts: str,
    symbol: str,
    last_price: Optional[float],
    mark_price: Optional[float],
) -> None:
    con.execute(
        """
        INSERT INTO ticker_snapshot (created_at, symbol, last_price, mark_price)
        VALUES (?, ?, ?, ?)
        """,
        (snapshot_ts, symbol, last_price, mark_price),
    )


def get_pending_limit_tasks(con) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT *
        FROM pending_limit_tasks
        WHERE status IN ('PENDING', 'TRIGGERED')
        ORDER BY id ASC
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        normalized_symbol = canonical_futures_symbol(item.get("symbol"))
        if normalized_symbol:
            item["symbol"] = normalized_symbol.split(":", 1)[0]
        out.append(item)
    return out


def get_pending_chase_tasks(con) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT *
        FROM pending_chase_tasks
        WHERE status = 'PENDING'
        ORDER BY id ASC
        """
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        normalized_symbol = canonical_futures_symbol(item.get("symbol"))
        if normalized_symbol:
            item["symbol"] = normalized_symbol.split(":", 1)[0]
        out.append(item)
    return out


def mark_pending_chase_baseline(
    con,
    task_id: int,
    baseline_contracts: float,
) -> None:
    con.execute(
        """
        UPDATE pending_chase_tasks
        SET baseline_contracts=?,
            updated_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (baseline_contracts, f" | baseline_contracts={baseline_contracts}", task_id),
    )


def mark_pending_chase_filled(
    con,
    task_id: int,
    live_contracts: float,
) -> None:
    con.execute(
        """
        UPDATE pending_chase_tasks
        SET status='FILLED',
            filled_contracts=?,
            updated_at=datetime('now'),
            resolved_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (live_contracts, f" | chase_filled_contracts={live_contracts}", task_id),
    )


def mark_pending_chase_expired(
    con,
    task_id: int,
    note_suffix: str,
) -> None:
    con.execute(
        """
        UPDATE pending_chase_tasks
        SET status='EXPIRED',
            updated_at=datetime('now'),
            resolved_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (note_suffix, task_id),
    )


def was_contracts_changed_past_baseline_since(
    con,
    symbol: str,
    side: str,
    baseline_contracts: float,
    created_at: str,
    panel_mode: str,
) -> bool:
    panel = str(panel_mode or "OPEN").upper()

    if panel == "CLOSE":
        row = con.execute(
            """
            SELECT 1
            FROM positions_snapshot
            WHERE symbol = ?
              AND side = ?
              AND created_at >= ?
              AND COALESCE(contracts, 0) < ?
            LIMIT 1
            """,
            (symbol, side, created_at, baseline_contracts),
        ).fetchone()
        return row is not None

    row = con.execute(
        """
        SELECT 1
        FROM positions_snapshot
        WHERE symbol = ?
          AND side = ?
          AND created_at >= ?
          AND COALESCE(contracts, 0) > ?
        LIMIT 1
        """,
        (symbol, side, created_at, baseline_contracts),
    ).fetchone()
    return row is not None


def was_limit_fill_seen_since(
    con,
    symbol: str,
    side: str,
    baseline_contracts: float,
    created_at: str,
    panel_mode: str,
) -> bool:
    panel = str(panel_mode or "OPEN").upper()

    if panel == "CLOSE":
        row = con.execute(
            """
            SELECT 1
            FROM positions_snapshot
            WHERE symbol = ?
              AND side = ?
              AND created_at >= ?
              AND COALESCE(contracts, 0) < ?
            LIMIT 1
            """,
            (symbol, side, created_at, baseline_contracts),
        ).fetchone()
        return row is not None

    row = con.execute(
        """
        SELECT 1
        FROM positions_snapshot
        WHERE symbol = ?
          AND side = ?
          AND created_at >= ?
          AND COALESCE(contracts, 0) > ?
        LIMIT 1
        """,
        (symbol, side, created_at, baseline_contracts),
    ).fetchone()
    return row is not None


def price_crossed_limit(
    panel_mode: str,
    side: str,
    limit_price: float,
    last_price: Optional[float],
) -> bool:
    if last_price is None:
        return False

    panel = str(panel_mode or "OPEN").upper()
    s = str(side or "").upper()

    is_buy_order = (
        (panel == "OPEN" and s == "LONG")
        or (panel == "CLOSE" and s == "SHORT")
    )

    if is_buy_order:
        return float(last_price) <= float(limit_price)

    return float(last_price) >= float(limit_price)


def mark_pending_limit_triggered(
    con,
    task_id: int,
    last_price: Optional[float],
    baseline_contracts: float,
) -> None:
    con.execute(
        """
        UPDATE pending_limit_tasks
        SET status='TRIGGERED',
            trigger_seen=1,
            baseline_contracts=?,
            triggered_at=datetime('now'),
            attempt_count=attempt_count+1,
            updated_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (baseline_contracts, f" | trigger_seen_at_price={last_price}", task_id),
    )


def mark_pending_limit_filled(
    con,
    task_id: int,
    last_price: Optional[float],
    live_contracts: float,
) -> None:
    con.execute(
        """
        UPDATE pending_limit_tasks
        SET status='FILLED',
            trigger_seen=1,
            updated_at=datetime('now'),
            resolved_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (f" | filled_seen_at_price={last_price} | live_contracts={live_contracts}", task_id),
    )


def mark_pending_limit_expired(
    con,
    task_id: int,
    note_suffix: str,
) -> None:
    con.execute(
        """
        UPDATE pending_limit_tasks
        SET status='EXPIRED',
            updated_at=datetime('now'),
            resolved_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (note_suffix, task_id),
    )


def mark_pending_limit_failed_after_trigger(
    con,
    task_id: int,
    note_suffix: str,
) -> None:
    con.execute(
        """
        UPDATE pending_limit_tasks
        SET status='FAILED_AFTER_TRIGGER',
            updated_at=datetime('now'),
            resolved_at=datetime('now'),
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (note_suffix, task_id),
    )


def is_limit_fill_detected(
    panel_mode: str,
    baseline_contracts: float,
    live_contracts: float,
) -> bool:
    mode = str(panel_mode or "OPEN").upper()

    if mode == "CLOSE":
        return float(live_contracts) < float(baseline_contracts)

    return float(live_contracts) > float(baseline_contracts)


def collect() -> None:
    print("COLLECTOR VERSION = v0.8 LIMIT-ONLY")
    ex = make_exchange()
    try:
        ex.load_markets()
    except Exception as e:
        print("load_markets failed (non-fatal):", e)

    con = connect()
    snapshot_ts = now_utc_iso()

    equity = free = mr = None
    try:
        bal = ex.fetch_balance()
        equity = bal.get("total", {}).get("USDT")
        free = bal.get("free", {}).get("USDT")

        info = bal.get("info", {}) if isinstance(bal, dict) else {}
        mr_val = info.get("marginRatio")
        mr = safe_float(mr_val, None) if mr_val is not None else None
    except Exception as e:
        print("fetch_balance failed:", e)

    con.execute(
        "INSERT INTO account_snapshot (created_at, equity, free_margin, margin_ratio) VALUES (?, ?, ?, ?)",
        (snapshot_ts, equity, free, mr),
    )

    prev_open = {}
    try:
        prev_open = get_latest_open_positions_from_db(con)
    except Exception as e:
        print("prev_open query failed (non-fatal):", e)
        prev_open = {}

    raw_positions: List[Dict[str, Any]] = []
    try:
        raw_positions = ex.fetch_positions()
    except Exception as e:
        print("fetch_positions failed:", e)

    positions = normalize_ccxt_positions(raw_positions)

    pending_limit_tasks = []
    if LEGACY_PENDING_LIMIT_TRACKING_ENABLED:
        try:
            pending_limit_tasks = get_pending_limit_tasks(con)
        except Exception as e:
            print("pending_limit_tasks query failed (non-fatal):", e)
            pending_limit_tasks = []

    pending_chase_tasks = []
    if LEGACY_PENDING_CHASE_TRACKING_ENABLED:
        try:
            pending_chase_tasks = get_pending_chase_tasks(con)
        except Exception as e:
            print("pending_chase_tasks query failed (non-fatal):", e)
            pending_chase_tasks = []

    watch_symbols: Set[str] = {p["symbol"] for p in positions if p.get("symbol")}
    if LEGACY_PENDING_LIMIT_TRACKING_ENABLED:
        watch_symbols |= {str(t["symbol"]) for t in pending_limit_tasks if t.get("symbol")}
    if LEGACY_PENDING_CHASE_TRACKING_ENABLED:
        watch_symbols |= {str(t["symbol"]) for t in pending_chase_tasks if t.get("symbol")}

    latest_prices: Dict[str, Optional[float]] = {}

    for sym in sorted(watch_symbols):
        try:
            t = ex.fetch_ticker(sym)

            last = safe_float(t.get("last"), None)

            info = t.get("info", {}) if isinstance(t, dict) else {}
            mark = safe_float(info.get("markPrice") or info.get("mark_price") or info.get("mark"), None)

            latest_prices[sym] = last
            insert_ticker_snapshot(con, snapshot_ts, sym, last, mark)
        except Exception as e:
            print(f"fetch_ticker failed for {sym}:", e)
            latest_prices[sym] = None
            continue

    live_keys: Set[Tuple[str, str]] = set()
    for p in positions:
        sym = p["symbol"]
        side = p["side"]
        live_keys.add(_pos_key(sym, side))

        insert_position_snapshot(
            con=con,
            snapshot_ts=snapshot_ts,
            symbol=sym,
            side=side,
            contracts=float(p["contracts"]),
            entry_price=float(p["entryPrice"]),
            unrealized_pnl=float(p["unrealizedPnl"]),
        )

    try:
        missing = [k for k in prev_open.keys() if k not in live_keys]
        for (sym, side) in missing:
            prev = prev_open[(sym, side)]
            insert_position_snapshot(
                con=con,
                snapshot_ts=snapshot_ts,
                symbol=sym,
                side=side,
                contracts=0.0,
                entry_price=float(prev.get("entryPrice", 0.0) or 0.0),
                unrealized_pnl=0.0,
            )
    except Exception as e:
        print("reconcile failed (non-fatal):", e)

    # ---------- pending LIMIT processing ----------
    if LEGACY_PENDING_LIMIT_TRACKING_ENABLED:
        try:
            live_contracts_map = {
                (p["symbol"], p["side"]): float(p["contracts"])
                for p in positions
            }

            for task in pending_limit_tasks:
                task_id = int(task["id"])
                action_id = task.get("action_id")
                sym = str(task["symbol"])
                side = str(task["side"]).upper()
                panel_mode = str(task.get("panel_mode") or "OPEN").upper()
                limit_price = float(task["limit_price"])
                task_status = str(task["status"]).upper()
                last_price = latest_prices.get(sym)

                prev_contracts = float(prev_open.get((sym, side), {}).get("contracts", 0.0) or 0.0)
                live_contracts = float(live_contracts_map.get((sym, side), 0.0) or 0.0)

                baseline_contracts = task.get("baseline_contracts")
                baseline_contracts = float(baseline_contracts) if baseline_contracts is not None else None

                created_at = task.get("created_at")
                triggered_at = task.get("triggered_at")

                base_for_detection = baseline_contracts if baseline_contracts is not None else prev_contracts

                crossed = price_crossed_limit(
                    panel_mode=panel_mode,
                    side=side,
                    limit_price=limit_price,
                    last_price=last_price,
                )

                if task_status == "PENDING":
                    age_row = con.execute(
                        """
                        SELECT CAST((julianday('now') - julianday(?)) * 24 * 60 AS INTEGER)
                        """,
                        (created_at,),
                    ).fetchone()
                    age_minutes = int(age_row[0] or 0)

                    if age_minutes >= PENDING_EXPIRE_MINUTES:
                        mark_pending_limit_expired(
                            con,
                            task_id=task_id,
                            note_suffix=f" | expired_pending_after_minutes={age_minutes}",
                        )
                        continue

                    history_seen_fill = was_limit_fill_seen_since(
                        con,
                        symbol=sym,
                        side=side,
                        baseline_contracts=base_for_detection,
                        created_at=created_at,
                        panel_mode=panel_mode,
                    )

                    if is_limit_fill_detected(panel_mode, base_for_detection, live_contracts) or history_seen_fill:
                        mark_pending_limit_filled(
                            con,
                            task_id=task_id,
                            last_price=last_price,
                            live_contracts=live_contracts,
                        )

                        if panel_mode == "OPEN":
                            qty = float(task.get("qty") or 0.0)
                            if qty > 0:
                                try:
                                    lot_id = create_lot_from_open_fill(
                                        con,
                                        symbol=sym,
                                        side=side,
                                        qty=qty,
                                        entry_price=float(limit_price),
                                        target_roi_pct=DEFAULT_LOT_TARGET_ROI_PCT,
                                        leverage=DEFAULT_LOT_LEVERAGE,
                                        opened_at=snapshot_ts,
                                        source_action_id=int(action_id) if action_id is not None else None,
                                        source_task_type="ACTION_QUEUE",
                                        source_task_id=int(action_id) if action_id is not None else task_id,
                                    )
                                    print(f"created lot {lot_id} for open limit fill task {task_id}")
                                except Exception as e:
                                    print(f"create lot failed for open limit task {task_id}:", e)

                        elif panel_mode == "CLOSE":
                            try:
                                matched = close_qty_against_lots(
                                    con,
                                    symbol=sym,
                                    side=side,
                                    close_qty=float(task.get("qty") or 0.0),
                                    close_price=float(limit_price),
                                    close_action_id=int(action_id) if action_id is not None else None,
                                    close_task_type="ACTION_QUEUE",
                                    close_task_id=int(action_id) if action_id is not None else task_id,
                                    note=str(task.get("note") or f"collector_close_limit_fill task_id={task_id}"),
                                    eligible_first=True,
                                )
                                print(f"lot realization on PENDING->FILLED for task {task_id}: {matched}")
                            except Exception as e:
                                print(f"lot realization failed for pending close limit task {task_id}:", e)

                        continue

                    if not crossed:
                        continue

                    mark_pending_limit_triggered(
                        con,
                        task_id=task_id,
                        last_price=last_price,
                        baseline_contracts=base_for_detection,
                    )

                elif task_status == "TRIGGERED":
                    base = baseline_contracts if baseline_contracts is not None else prev_contracts

                    history_seen_fill = was_limit_fill_seen_since(
                        con,
                        symbol=sym,
                        side=side,
                        baseline_contracts=base,
                        created_at=(triggered_at or created_at),
                        panel_mode=panel_mode,
                    )

                    if is_limit_fill_detected(panel_mode, base, live_contracts) or history_seen_fill:
                        mark_pending_limit_filled(
                            con,
                            task_id=task_id,
                            last_price=last_price,
                            live_contracts=live_contracts,
                        )

                        if panel_mode == "OPEN":
                            qty = float(task.get("qty") or 0.0)
                            if qty > 0:
                                try:
                                    lot_id = create_lot_from_open_fill(
                                        con,
                                        symbol=sym,
                                        side=side,
                                        qty=qty,
                                        entry_price=float(limit_price),
                                        target_roi_pct=DEFAULT_LOT_TARGET_ROI_PCT,
                                        leverage=DEFAULT_LOT_LEVERAGE,
                                        opened_at=snapshot_ts,
                                        source_action_id=int(action_id) if action_id is not None else None,
                                        source_task_type="ACTION_QUEUE",
                                        source_task_id=int(action_id) if action_id is not None else task_id,
                                    )
                                    print(f"created lot {lot_id} for triggered open limit fill task {task_id}")
                                except Exception as e:
                                    print(f"create lot failed for triggered open limit task {task_id}:", e)

                        elif panel_mode == "CLOSE":
                            try:
                                matched = close_qty_against_lots(
                                    con,
                                    symbol=sym,
                                    side=side,
                                    close_qty=float(task.get("qty") or 0.0),
                                    close_price=float(limit_price),
                                    close_action_id=int(action_id) if action_id is not None else None,
                                    close_task_type="ACTION_QUEUE",
                                    close_task_id=int(action_id) if action_id is not None else task_id,
                                    note=str(task.get("note") or f"collector_close_limit_fill task_id={task_id}"),
                                    eligible_first=True,
                                )
                                print(f"lot realization on TRIGGERED->FILLED for task {task_id}: {matched}")
                            except Exception as e:
                                print(f"lot realization failed for triggered close limit task {task_id}:", e)

                        continue

                    trig_row = con.execute(
                        """
                        SELECT CAST((julianday('now') - julianday(?)) * 24 * 60 AS INTEGER)
                        """,
                        (triggered_at or created_at,),
                    ).fetchone()
                    trig_minutes = int(trig_row[0] or 0)

                    if trig_minutes >= TRIGGERED_FAIL_MINUTES:
                        mark_pending_limit_failed_after_trigger(
                            con,
                            task_id=task_id,
                            note_suffix=f" | failed_after_trigger_minutes={trig_minutes}",
                        )

        except Exception as e:
            print("pending limit processing failed (non-fatal):", e)

    # ---------- pending CHASE processing [legacy only] ----------
    if LEGACY_PENDING_CHASE_TRACKING_ENABLED:
        try:
            live_contracts_map = {
                (p["symbol"], p["side"]): float(p["contracts"])
                for p in positions
            }

            for task in pending_chase_tasks:
                task_id = int(task["id"])
                sym = str(task["symbol"])
                side = str(task["side"]).upper()
                task_status = str(task["status"]).upper()
                panel_mode = str(task.get("panel_mode") or "OPEN").upper()

                if task_status != "PENDING":
                    continue

                live_contracts = float(live_contracts_map.get((sym, side), 0.0) or 0.0)

                baseline_contracts = task.get("baseline_contracts")
                baseline_contracts = float(baseline_contracts) if baseline_contracts is not None else None

                created_at = task.get("created_at")

                if baseline_contracts is None:
                    mark_pending_chase_baseline(
                        con,
                        task_id=task_id,
                        baseline_contracts=live_contracts,
                    )
                    continue

                history_seen_fill = was_contracts_changed_past_baseline_since(
                    con,
                    symbol=sym,
                    side=side,
                    baseline_contracts=baseline_contracts,
                    created_at=created_at,
                    panel_mode=panel_mode,
                )

                is_open_fill = panel_mode == "OPEN" and live_contracts > baseline_contracts
                is_close_fill = panel_mode == "CLOSE" and live_contracts < baseline_contracts

                if is_open_fill or is_close_fill or history_seen_fill:
                    fill_contracts = live_contracts
                    mark_pending_chase_filled(
                        con,
                        task_id=task_id,
                        live_contracts=fill_contracts,
                    )
                    continue

                age_row = con.execute(
                    """
                    SELECT CAST((julianday('now') - julianday(?)) * 24 * 60 AS INTEGER)
                    """,
                    (created_at,),
                ).fetchone()
                age_minutes = int(age_row[0] or 0)

                if age_minutes >= CHASE_EXPIRE_MINUTES:
                    mark_pending_chase_expired(
                        con,
                        task_id=task_id,
                        note_suffix=f" | chase_expired_after_minutes={age_minutes}",
                    )

        except Exception as e:
            print("pending chase processing failed (non-fatal):", e)

    con.commit()
    con.close()
    print("Collector run OK")


if __name__ == "__main__":
    collect()

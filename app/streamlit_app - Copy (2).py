# app/streamlit_app.py
# FULL FILE — Strategy v2 + SAFE MODE (1 contract per action)
# OPEN uses LIMIT
# CLOSE uses LIMIT
# CHASE kept only as legacy read-only visibility

import math
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# Ensure project root on sys.path so "core" imports work
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import connect  # noqa: E402
from core.open_classifier import classify_open_action  # noqa: E402
from core.close_classifier import classify_close_action  # noqa: E402


# ---------- Helpers ----------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float_series(df: pd.DataFrame, col: str) -> None:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)


def risk_flag(row, equity):
    gross = abs(float(row.get("gross_contracts") or 0.0))
    net = abs(float(row.get("net_contracts") or 0.0))

    if gross == 0:
        return "—"

    if net == gross:
        return "🔴 HIGH RISK – REDUCE ONLY"

    hedge_ratio = 1 - (net / gross)

    if hedge_ratio >= 0.75:
        return "🟢 HARVEST"
    if hedge_ratio >= 0.40:
        return "🟡 CONTROL"
    return "🟠 CAUTION"


def suggest_next_action(row):
    gross = float(row.get("gross_contracts") or 0.0)
    net = float(row.get("net_contracts") or 0.0)
    flag = str(row.get("risk_flag") or "")

    if gross == 0:
        return "No position."

    if abs(net) == abs(gross):
        side = "SHORT" if net < 0 else "LONG"
        return (
            f"One-sided {side}. Reduce only: close 10–15% on the next favorable move. "
            f"No adds; emergency hedge only if margin becomes tight."
        )

    hedge_ratio = 1 - (abs(net) / abs(gross)) if gross else 0.0

    if "HARVEST" in flag or hedge_ratio >= 0.75:
        return (
            "Hedged. Harvest oscillation: trim 5–10% from the profitable leg on a spike, "
            "then wait. Goal: reduce gross gradually while keeping net small."
        )

    if "CONTROL" in flag or hedge_ratio >= 0.40:
        return (
            "Partially hedged. Prefer trims over adds: trim the profitable side 5–10% at relief moves. "
            "Avoid increasing gross."
        )

    return (
        "Caution. Net exposure is relatively large: prioritize gross reduction. "
        "Trim profitable side first; avoid adding into moves."
    )


# ---------- Pending LIMIT helpers ----------
def get_pending_limit_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pending_limit_tasks
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "TRIGGERED": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "FAILED_AFTER_TRIGGER": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def list_pending_limit_tasks(con, status: str = "ALL", limit: int = 200):
    if status == "ALL":
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, limit_price, qty, status,
                   trigger_seen, baseline_contracts, triggered_at,
                   attempt_count, created_at, updated_at, resolved_at, note
            FROM pending_limit_tasks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, limit_price, qty, status,
                   trigger_seen, baseline_contracts, triggered_at,
                   attempt_count, created_at, updated_at, resolved_at, note
            FROM pending_limit_tasks
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return rows


def reset_pending_limit_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET status='PENDING',
            trigger_seen=0,
            baseline_contracts=NULL,
            triggered_at=NULL,
            resolved_at=NULL,
            attempt_count=0,
            updated_at=datetime('now'),
            created_at=datetime('now'),
            note='RESET_FOR_TEST'
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def set_pending_limit_created_now(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET created_at=datetime('now'),
            updated_at=datetime('now')
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def delete_resolved_pending_limit_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
        """
    )
    con.commit()
    return cur.rowcount


def delete_pending_limit_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def mark_pending_limit_task_status(con, task_id: int, new_status: str, note_suffix: str = "") -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET status=?,
            updated_at=datetime('now'),
            resolved_at=CASE
                WHEN ? IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
                THEN datetime('now')
                ELSE resolved_at
            END,
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (new_status, new_status, note_suffix, task_id),
    )
    con.commit()
    return cur.rowcount


# ---------- Pending CHASE helpers (legacy read-only support) ----------
def get_pending_chase_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pending_chase_tasks
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "FAILED": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def list_pending_chase_tasks(con, status: str = "ALL", limit: int = 200):
    if status == "ALL":
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, qty, status,
                   baseline_contracts, filled_contracts,
                   created_at, updated_at, resolved_at, note
            FROM pending_chase_tasks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, qty, status,
                   baseline_contracts, filled_contracts,
                   created_at, updated_at, resolved_at, note
            FROM pending_chase_tasks
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return rows


def reset_pending_chase_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET status='PENDING',
            baseline_contracts=NULL,
            filled_contracts=NULL,
            resolved_at=NULL,
            updated_at=datetime('now'),
            created_at=datetime('now'),
            note='RESET_FOR_TEST'
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def set_pending_chase_created_now(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET created_at=datetime('now'),
            updated_at=datetime('now')
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def delete_resolved_pending_chase_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_chase_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED')
        """
    )
    con.commit()
    return cur.rowcount


def delete_pending_chase_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_chase_tasks
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def mark_pending_chase_task_status(con, task_id: int, new_status: str, note_suffix: str = "") -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET status=?,
            updated_at=datetime('now'),
            resolved_at=CASE
                WHEN ? IN ('FILLED', 'EXPIRED', 'FAILED')
                THEN datetime('now')
                ELSE resolved_at
            END,
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (new_status, new_status, note_suffix, task_id),
    )
    con.commit()
    return cur.rowcount


# ---------- DB Readers/Writers ----------
def get_latest_account_snapshot(con):
    row = con.execute(
        """
        SELECT *
        FROM account_snapshot
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def get_latest_positions_per_symbol_side(con):
    rows = con.execute(
        """
        SELECT p.symbol, p.side, p.contracts, p.entry_price, p.unrealized_pnl, p.created_at
        FROM positions_snapshot p
        JOIN (
            SELECT symbol, side, MAX(id) AS max_id
            FROM positions_snapshot
            GROUP BY symbol, side
        ) latest
        ON p.id = latest.max_id
        ORDER BY p.symbol ASC, p.side ASC
        """
    ).fetchall()

    out = [dict(r) for r in rows]
    latest_ts = max((r.get("created_at") for r in out if r.get("created_at")), default=None)
    return latest_ts, out


def add_action_ledger(con, created_at, symbol, action_type, side=None, qty=None, price=None, note=None):
    con.execute(
        """
        INSERT INTO actions_ledger (created_at, symbol, action_type, side, qty, price, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, symbol, action_type, side, qty, price, note),
    )


def get_recent_actions(con, limit=50):
    rows = con.execute(
        """
        SELECT created_at, symbol, action_type, side, qty, price, note
        FROM actions_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_tickers(con):
    rows = con.execute(
        """
        SELECT t.symbol, t.last_price, t.mark_price, t.created_at
        FROM ticker_snapshot t
        JOIN (
            SELECT symbol, MAX(id) AS max_id
            FROM ticker_snapshot
            GROUP BY symbol
        ) latest
        ON t.id = latest.max_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_ticker_price_for_symbol(con, symbol: str) -> Optional[float]:
    row = con.execute(
        """
        SELECT last_price
        FROM ticker_snapshot
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    if not row:
        return None

    try:
        v = row["last_price"]
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def list_position_lots(con, symbol: str = "ALL", status: str = "ALL", limit: int = 500):
    sql = """
        SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
               target_roi_pct, leverage, target_price, opened_at,
               source_action_id, source_task_type, source_task_id, status
        FROM position_lots
    """
    clauses = []
    params = []

    if symbol != "ALL":
        clauses.append("symbol = ?")
        params.append(symbol)

    if status != "ALL":
        clauses.append("status = ?")
        params.append(status)

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_lot_realizations(con, symbol: str = "ALL", limit: int = 500):
    sql = """
        SELECT id, lot_id, symbol, side, close_qty, entry_price, close_price,
               target_price, realized_roi_pct, closed_at,
               close_action_id, close_task_type, close_task_id, note
        FROM lot_realizations
    """
    params = []

    if symbol != "ALL":
        sql += " WHERE symbol = ?"
        params.append(symbol)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def get_distinct_lot_symbols(con):
    rows = con.execute(
        """
        SELECT symbol
        FROM (
            SELECT symbol FROM position_lots
            UNION
            SELECT symbol FROM lot_realizations
        )
        WHERE symbol IS NOT NULL AND symbol <> ''
        ORDER BY symbol ASC
        """
    ).fetchall()
    return [str(r["symbol"]) for r in rows]


def compute_eligible_lots_df(df_lots: pd.DataFrame, current_price: Optional[float]) -> pd.DataFrame:
    if df_lots.empty:
        return df_lots.copy()

    df = df_lots.copy()
    if current_price is None or current_price <= 0:
        df["eligible_now"] = False
        return df.iloc[0:0].copy()

    def _is_eligible(row) -> bool:
        side = str(row.get("side") or "").upper()
        target_price = float(row.get("target_price") or 0.0)
        qty_remaining = float(row.get("qty_remaining") or 0.0)
        status = str(row.get("status") or "").upper()

        if status != "OPEN" or qty_remaining <= 0 or target_price <= 0:
            return False

        if side == "LONG":
            return float(current_price) >= target_price
        if side == "SHORT":
            return float(current_price) <= target_price
        return False

    df["eligible_now"] = df.apply(_is_eligible, axis=1)
    return df[df["eligible_now"]].copy()


def get_open_lots_for_symbol(con, symbol: str):
    rows = con.execute(
        """
        SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
               target_roi_pct, leverage, target_price, opened_at,
               source_action_id, source_task_type, source_task_id, status
        FROM position_lots
        WHERE symbol = ?
          AND status = 'OPEN'
          AND COALESCE(qty_remaining, 0) > 0
        ORDER BY id ASC
        """,
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Action Queue (v2) helpers ----------
def queue_insert_v2(con, p: dict) -> int:
    p = dict(p)
    p["idempotency_key"] = uuid.uuid4().hex

    cur = con.execute(
        """
        INSERT INTO action_queue (
          created_at, created_by, exchange, market_type, symbol, intent, side,
          reduce_only, qty, qty_unit, order_type, limit_price, slippage_bps,
          trigger_type, trigger_op, trigger_price, trigger_timeout_sec,
          min_free_margin, max_margin_ratio, max_spread_bps,
          status, priority, idempotency_key, attempts, last_error, last_update_at,
          ui_hint, note, panel_mode, order_kind, leverage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["created_at"],
            p.get("created_by", "streamlit"),
            p.get("exchange", "mexc"),
            p.get("market_type", "swap"),
            p["symbol"],
            p.get("intent", "close"),
            p.get("side"),
            int(p.get("reduce_only", 1)),
            float(p["qty"]),
            p.get("qty_unit", "contracts"),
            p.get("order_type", "market"),
            p.get("limit_price"),
            p.get("slippage_bps"),
            p.get("trigger_type", "manual"),
            p.get("trigger_op"),
            p.get("trigger_price"),
            p.get("trigger_timeout_sec"),
            p.get("min_free_margin"),
            p.get("max_margin_ratio"),
            p.get("max_spread_bps"),
            p.get("status", "PENDING"),
            int(p.get("priority", 100)),
            p["idempotency_key"],
            int(p.get("attempts", 0)),
            p.get("last_error"),
            p.get("last_update_at"),
            p.get("ui_hint"),
            p.get("note"),
            p.get("panel_mode", "CLOSE"),
            p.get("order_kind", "LIMIT"),
            p.get("leverage"),
        ),
    )
    return int(cur.rowcount)


def queue_list_v2(con, status=None, limit=200):
    if status and status != "ALL":
        rows = con.execute(
            """
            SELECT *
            FROM action_queue
            WHERE status=?
            ORDER BY priority ASC, id ASC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT *
            FROM action_queue
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_action_queue_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM action_queue
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "ARMED": 0,
        "RUNNING": 0,
        "DONE": 0,
        "FAILED": 0,
        "CANCELED": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def queue_arm(con, action_id: int):
    con.execute(
        "UPDATE action_queue SET status='ARMED', last_update_at=? WHERE id=? AND status='PENDING'",
        (now_utc_iso(), action_id),
    )


def queue_cancel(con, action_id: int):
    con.execute(
        "UPDATE action_queue SET status='CANCELED', last_update_at=? WHERE id=? AND status IN ('PENDING','ARMED')",
        (now_utc_iso(), action_id),
    )


def delete_action_queue_by_statuses(con, statuses: list[str]) -> int:
    if not statuses:
        return 0

    placeholders = ",".join(["?"] * len(statuses))
    cur = con.execute(
        f"""
        DELETE FROM action_queue
        WHERE status IN ({placeholders})
        """,
        tuple(statuses),
    )
    con.commit()
    return int(cur.rowcount)


def delete_action_queue_row(con, action_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM action_queue
        WHERE id=?
        """,
        (action_id,),
    )
    con.commit()
    return int(cur.rowcount)


# ---------- Debug DB path ----------
def get_db_files(con):
    rows = con.execute("PRAGMA database_list;").fetchall()
    out = []
    for r in rows:
        try:
            out.append({"seq": r[0], "name": r[1], "file": r[2]})
        except Exception:
            out.append({"row": str(r)})
    return out


# ---------- Strategy helpers ----------
def has_active_task_for_symbol(con, symbol: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM action_queue
        WHERE symbol=?
          AND status IN ('ARMED','RUNNING')
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def has_active_pending_limit_for_symbol(con, symbol: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM pending_limit_tasks
        WHERE symbol=?
          AND status IN ('PENDING', 'TRIGGERED')
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def has_active_pending_chase_for_symbol(con, symbol: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM pending_chase_tasks
        WHERE symbol=?
          AND status = 'PENDING'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def enqueue_armed_open_limit(
    con,
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    note: str,
    priority: int = 50,
) -> None:
    p = {
        "created_at": now_utc_iso(),
        "created_by": "streamlit:auto",
        "exchange": "mexc",
        "market_type": "swap",
        "symbol": symbol.strip(),
        "intent": "open",
        "side": side.upper(),
        "reduce_only": 0,
        "qty": float(qty),
        "qty_unit": "contracts",
        "order_type": "limit",
        "limit_price": float(limit_price),
        "slippage_bps": None,
        "trigger_type": "immediate",
        "trigger_op": None,
        "trigger_price": None,
        "trigger_timeout_sec": None,
        "min_free_margin": None,
        "max_margin_ratio": None,
        "max_spread_bps": None,
        "status": "ARMED",
        "priority": int(priority),
        "attempts": 0,
        "last_error": None,
        "last_update_at": now_utc_iso(),
        "ui_hint": None,
        "note": note.strip() if note else None,
        "panel_mode": "OPEN",
        "order_kind": "LIMIT",
        "leverage": None,
    }
    queue_insert_v2(con, p)
    con.commit()


def enqueue_armed_close_limit(
    con,
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    note: str,
    priority: int = 40,
) -> None:
    p = {
        "created_at": now_utc_iso(),
        "created_by": "streamlit:auto",
        "exchange": "mexc",
        "market_type": "swap",
        "symbol": symbol.strip(),
        "intent": "close",
        "side": side.upper(),
        "reduce_only": 1,
        "qty": float(qty),
        "qty_unit": "contracts",
        "order_type": "limit",
        "limit_price": float(limit_price),
        "slippage_bps": None,
        "trigger_type": "immediate",
        "trigger_op": None,
        "trigger_price": None,
        "trigger_timeout_sec": None,
        "min_free_margin": None,
        "max_margin_ratio": None,
        "max_spread_bps": None,
        "status": "ARMED",
        "priority": int(priority),
        "attempts": 0,
        "last_error": None,
        "last_update_at": now_utc_iso(),
        "ui_hint": None,
        "note": note.strip() if note else None,
        "panel_mode": "CLOSE",
        "order_kind": "LIMIT",
        "leverage": None,
    }
    queue_insert_v2(con, p)
    con.commit()


def get_leg(df_pos: pd.DataFrame, symbol: str, side: str) -> dict:
    if df_pos.empty:
        return {"contracts": 0.0, "unrealized_pnl": 0.0, "entry_price": 0.0}

    x = df_pos[(df_pos["symbol"] == symbol) & (df_pos["side"] == side)]
    if x.empty:
        return {"contracts": 0.0, "unrealized_pnl": 0.0, "entry_price": 0.0}

    r = x.iloc[0].to_dict()
    return {
        "contracts": float(r.get("contracts") or 0.0),
        "unrealized_pnl": float(r.get("unrealized_pnl") or 0.0),
        "entry_price": float(r.get("entry_price") or 0.0),
    }


def get_strategy_regime_params(starting_capital: float, secured_capital: float) -> dict:
    if float(secured_capital) < float(starting_capital):
        return {
            "regime": "RECOVERY",
            "initial_entry_pct": 20.0,
            "rebalance_step_pct": 10.0,
            "max_symbol_envelope_pct": 50.0,
            "trim_winner_pct": 10.0,
            "profit_recycle_pct": 50.0,
            "max_action_contracts_cap": 100.0,
            "cooldown_sec": 10.0,
        }
    return {
        "regime": "AGGRESSIVE",
        "initial_entry_pct": 35.0,
        "rebalance_step_pct": 15.0,
        "max_symbol_envelope_pct": 70.0,
        "trim_winner_pct": 12.5,
        "profit_recycle_pct": 80.0,
        "max_action_contracts_cap": 250.0,
        "cooldown_sec": 8.0,
    }


def floor_to_contract_step(raw_contracts: float, step: float = 1.0) -> float:
    if raw_contracts <= 0 or step <= 0:
        return 0.0
    return math.floor(raw_contracts / step) * step


def estimate_used_symbol_margin(long_contracts: float, short_contracts: float, last_price: Optional[float], leverage: float) -> float:
    if last_price is None or last_price <= 0 or leverage <= 0:
        return 0.0
    gross_contracts = max(0.0, float(long_contracts)) + max(0.0, float(short_contracts))
    gross_notional = gross_contracts * float(last_price)
    return gross_notional / float(leverage)


def margin_budget_to_contracts(
    margin_budget: float,
    last_price: Optional[float],
    leverage: float,
    contract_step: float = 1.0,
) -> float:
    if margin_budget <= 0 or last_price is None or last_price <= 0 or leverage <= 0:
        return 0.0
    raw = (float(margin_budget) * float(leverage)) / float(last_price)
    return floor_to_contract_step(raw, contract_step)


def compute_strategy_sizing(
    *,
    active_strategy_capital: float,
    last_price: Optional[float],
    leverage: float,
    long_contracts: float,
    short_contracts: float,
    initial_entry_pct: float,
    rebalance_step_pct: float,
    max_symbol_envelope_pct: float,
    max_action_contracts_cap: float,
    min_action_contracts: float = 1.0,
    contract_step: float = 1.0,
) -> dict:
    active_strategy_capital = max(0.0, float(active_strategy_capital))
    symbol_envelope_margin = active_strategy_capital * (float(max_symbol_envelope_pct) / 100.0)
    used_symbol_margin = estimate_used_symbol_margin(long_contracts, short_contracts, last_price, leverage)
    available_symbol_margin = max(0.0, symbol_envelope_margin - used_symbol_margin)

    requested_initial_margin_budget = active_strategy_capital * (float(initial_entry_pct) / 100.0)
    requested_rebalance_margin_budget = active_strategy_capital * (float(rebalance_step_pct) / 100.0)

    effective_initial_margin_budget = min(requested_initial_margin_budget, available_symbol_margin)
    effective_rebalance_margin_budget = min(requested_rebalance_margin_budget, available_symbol_margin)

    raw_initial_contracts = margin_budget_to_contracts(
        effective_initial_margin_budget, last_price, leverage, contract_step
    )
    raw_rebalance_contracts = margin_budget_to_contracts(
        effective_rebalance_margin_budget, last_price, leverage, contract_step
    )

    final_initial_contracts = min(raw_initial_contracts, float(max_action_contracts_cap))
    final_rebalance_contracts = min(raw_rebalance_contracts, float(max_action_contracts_cap))

    if final_initial_contracts < float(min_action_contracts):
        final_initial_contracts = 0.0
    if final_rebalance_contracts < float(min_action_contracts):
        final_rebalance_contracts = 0.0

    return {
        "symbol_envelope_margin": symbol_envelope_margin,
        "used_symbol_margin": used_symbol_margin,
        "available_symbol_margin": available_symbol_margin,
        "requested_initial_margin_budget": requested_initial_margin_budget,
        "requested_rebalance_margin_budget": requested_rebalance_margin_budget,
        "effective_initial_margin_budget": effective_initial_margin_budget,
        "effective_rebalance_margin_budget": effective_rebalance_margin_budget,
        "raw_initial_contracts": raw_initial_contracts,
        "raw_rebalance_contracts": raw_rebalance_contracts,
        "final_initial_contracts": final_initial_contracts,
        "final_rebalance_contracts": final_rebalance_contracts,
    }


def compute_imbalance_ratio(long_contracts: float, short_contracts: float) -> float:
    l = max(0.0, float(long_contracts))
    s = max(0.0, float(short_contracts))
    smaller = min(l, s)
    bigger = max(l, s)
    if smaller <= 0:
        return float("inf") if bigger > 0 else 0.0
    return bigger / smaller


def build_trim_decision(
    *,
    winner_side: str,
    long_contracts: float,
    short_contracts: float,
    long_target_price: Optional[float],
    short_target_price: Optional[float],
    trim_winner_pct: float,
    limit_offset_pct: float,
    safe_mode_one_contract: bool,
    contract_step: float,
    reason_suffix: str,
) -> tuple | None:
    winner_side = str(winner_side).upper()

    winner_contracts = long_contracts if winner_side == "LONG" else short_contracts
    if winner_contracts <= 0:
        return None

    trim_contracts = 1.0 if safe_mode_one_contract else max(
        1.0,
        floor_to_contract_step(
            winner_contracts * (float(trim_winner_pct) / 100.0),
            float(contract_step),
        ),
    )
    trim_contracts = float(min(trim_contracts, winner_contracts))

    if trim_contracts < 1:
        return None

    if winner_side == "LONG":
        if long_target_price is None:
            return None
        price_ref = float(long_target_price)
        limit_price = price_ref * (1.0 + float(limit_offset_pct) / 100.0)
    else:
        if short_target_price is None:
            return None
        price_ref = float(short_target_price)
        limit_price = price_ref * (1.0 - float(limit_offset_pct) / 100.0)

    return (
        "CLOSE_LIMIT",
        winner_side,
        trim_contracts,
        limit_price,
        f"auto: trim {winner_side} winner | {reason_suffix}",
    )


def has_valid_rebalance_signal(
    *,
    smaller_side: str,
    last_price: Optional[float],
    long_entry: float,
    short_entry: float,
    rebalance_trigger_pct: float,
) -> bool:
    if last_price is None or last_price <= 0:
        return False

    trigger_frac = float(rebalance_trigger_pct) / 100.0
    side = str(smaller_side).upper()

    if side == "LONG" and long_entry > 0:
        return float(last_price) >= float(long_entry) * (1.0 + trigger_frac)

    if side == "SHORT" and short_entry > 0:
        return float(last_price) <= float(short_entry) * (1.0 - trigger_frac)

    return False


def build_open_limit_price(
    *,
    side: str,
    current_last_price: Optional[float],
    open_limit_offset_pct: float,
) -> Optional[float]:
    if current_last_price is None or current_last_price <= 0:
        return None

    side_u = str(side).upper()

    if side_u == "LONG":
        return float(current_last_price) * (1.0 - float(open_limit_offset_pct) / 100.0)

    if side_u == "SHORT":
        return float(current_last_price) * (1.0 + float(open_limit_offset_pct) / 100.0)

    return None


def get_winner_side_from_lots_or_leg(
    *,
    lot_trim_ready: bool,
    eligible_lots_df: pd.DataFrame,
    long_ready: bool,
    short_ready: bool,
    long_upnl: float,
    short_upnl: float,
) -> str:
    if lot_trim_ready and not eligible_lots_df.empty:
        side_qty = (
            eligible_lots_df.groupby("side", as_index=False)["qty_remaining"]
            .sum()
            .sort_values(["qty_remaining", "side"], ascending=[False, True])
        )
        return str(side_qty.iloc[0]["side"]).upper()

    if long_ready and short_ready:
        return "LONG" if long_upnl >= short_upnl else "SHORT"
    if long_ready:
        return "LONG"
    return "SHORT"


# ---------- Reset stuck tasks ----------
def reset_running(con, symbol: Optional[str] = None, also_reset_armed: bool = False) -> int:
    statuses = ["RUNNING"]
    if also_reset_armed:
        statuses.append("ARMED")

    if symbol:
        cur = con.execute(
            f"""
            UPDATE action_queue
            SET status='FAILED',
                last_error=COALESCE(last_error, 'manual reset (stuck)'),
                last_update_at=datetime('now')
            WHERE symbol=?
              AND status IN ({",".join(["?"] * len(statuses))})
            """,
            (symbol, *statuses),
        )
    else:
        cur = con.execute(
            f"""
            UPDATE action_queue
            SET status='FAILED',
                last_error=COALESCE(last_error, 'manual reset (stuck)'),
                last_update_at=datetime('now')
            WHERE status IN ({",".join(["?"] * len(statuses))})
            """,
            (*statuses,),
        )
    con.commit()
    return int(cur.rowcount)


# ---------- UI ----------
st.set_page_config(page_title="MEXC Monitor", layout="wide")
st.title("MEXC Monitor (read-only)")

con = connect()

with st.sidebar:
    st.subheader("Debug")
    if st.checkbox("Show DB file path(s)", value=True):
        st.write(get_db_files(con))

    st.divider()
    st.subheader("Emergency tools (DB)")
    st.caption("Use only if tasks are stuck RUNNING and block the Strategy guard.")
    sym_reset = st.text_input("Symbol to reset (optional)", value="").strip()
    also_armed = st.checkbox("Also reset ARMED", value=False)

    c1, c2 = st.columns(2)
    if c1.button("Reset RUNNING (symbol)"):
        if not sym_reset:
            st.error("Enter a symbol (e.g. ADA/USDT).")
        else:
            n = reset_running(con, symbol=sym_reset, also_reset_armed=also_armed)
            st.success(f"Reset {n} rows for {sym_reset}.")
            st.rerun()

    if c2.button("Reset RUNNING (ALL)"):
        n = reset_running(con, symbol=None, also_reset_armed=also_armed)
        st.success(f"Reset {n} rows (ALL symbols).")
        st.rerun()


tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "Dashboard",
    "Action Queue",
    "Action Ledger",
    "Strategy (AUTO)",
    "Pending LIMIT",
    "Pending CHASE",
    "Lots",
])

# =================== Dashboard ===================
with tab1:
    st.subheader("Live data (collector snapshots)")

    cA, cB, cC = st.columns([1, 1, 2])
    show_closed_legs = cA.checkbox("Show CLOSED legs (contracts=0)", value=False)
    aggregates_open_only = cB.checkbox("Aggregates from OPEN legs only", value=True)
    cC.caption("If a leg is closed, collector should write a latest row with contracts=0.")

    acc = get_latest_account_snapshot(con)
    pos_ts, pos_rows = get_latest_positions_per_symbol_side(con)

    c1, c2, c3, c4 = st.columns(4)
    if acc:
        c1.metric("Equity (USDT)", f"{acc['equity']:.4f}" if acc.get("equity") is not None else "—")
        c2.metric("Free margin (USDT)", f"{acc['free_margin']:.4f}" if acc.get("free_margin") is not None else "—")
        c3.metric("Margin ratio", f"{acc['margin_ratio']:.4f}" if acc.get("margin_ratio") is not None else "—")
        c4.metric("Account snapshot (UTC)", acc.get("created_at", "—"))
    else:
        c1.metric("Equity (USDT)", "—")
        c2.metric("Free margin (USDT)", "—")
        c3.metric("Margin ratio", "—")
        c4.metric("Account snapshot (UTC)", "—")

    st.caption(f"Latest positions per (symbol, side) timestamp: {pos_ts or '—'}")

    if pos_rows:
        df_pos = pd.DataFrame(pos_rows)

        _to_float_series(df_pos, "contracts")
        _to_float_series(df_pos, "entry_price")
        _to_float_series(df_pos, "unrealized_pnl")

        tick_rows = get_latest_tickers(con)
        if tick_rows:
            df_tick = pd.DataFrame(tick_rows)
            _to_float_series(df_tick, "last_price")
            _to_float_series(df_tick, "mark_price")

            df_pos = df_pos.merge(
                df_tick[["symbol", "last_price", "mark_price", "created_at"]].rename(
                    columns={"created_at": "ticker_at"}
                ),
                on="symbol",
                how="left",
            )

        df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
        df_pos["status"] = df_pos["contracts"].apply(lambda x: "OPEN" if float(x or 0.0) > 0 else "CLOSED")

        df_pos_view = df_pos[df_pos["status"] == "OPEN"].copy() if not show_closed_legs else df_pos.copy()

        df_pos_view["signed_contracts"] = df_pos_view.apply(
            lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
            axis=1,
        )

        df_for_agg = df_pos[df_pos["status"] == "OPEN"].copy() if aggregates_open_only else df_pos.copy()

        df_for_agg["signed_contracts"] = df_for_agg.apply(
            lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
            axis=1,
        )

        df_agg = (
            df_for_agg.groupby("symbol", as_index=False)
            .agg(
                total_unrealized=("unrealized_pnl", "sum"),
                net_contracts=("signed_contracts", "sum"),
                gross_contracts=("contracts", "sum"),
            )
            .sort_values("total_unrealized")
        )

        equity_val = acc.get("equity") if acc and acc.get("equity") else None
        df_agg["risk_flag"] = df_agg.apply(lambda r: risk_flag(r, equity_val), axis=1)
        df_agg["suggested_next_action"] = df_agg.apply(suggest_next_action, axis=1)

        left, right = st.columns([2, 1])

        with left:
            st.write("### Positions (latest per symbol+side)")
            cols_show = ["symbol", "side", "status", "contracts", "entry_price", "last_price", "unrealized_pnl", "created_at", "ticker_at"]
            st.dataframe(df_pos_view[cols_show], width="stretch")

        with right:
            st.write("### Aggregates by symbol (risk + suggestion)")
            st.dataframe(
                df_agg[
                    [
                        "symbol",
                        "risk_flag",
                        "gross_contracts",
                        "net_contracts",
                        "total_unrealized",
                        "suggested_next_action",
                    ]
                ],
                width="stretch",
            )

        losers = df_agg.nsmallest(5, "total_unrealized")
        winners = df_agg.nlargest(5, "total_unrealized")

        l1, l2 = st.columns(2)
        with l1:
            st.write("### Top losers (unrealized)")
            st.dataframe(losers, width="stretch")
        with l2:
            st.write("### Top winners (unrealized)")
            st.dataframe(winners, width="stretch")

        st.write("### Suggested next actions (per symbol)")
        for _, r in df_agg.iterrows():
            st.write(f"**{r['symbol']}** — {r['risk_flag']}")
            st.write(r["suggested_next_action"])
            st.write("")
    else:
        st.info("No positions_snapshot data yet. Run: python -m core.collector")


# =================== Action Queue ===================
with tab2:
    st.subheader("Action Queue (PENDING → ARM → Tampermonkey executes ARMED)")
    st.caption("Manual queue creation and strategy both use LIMIT only. CHASE stays visible only for legacy history.")

    qcounts = get_action_queue_status_counts(con)

    q1, q2, q3, q4, q5, q6 = st.columns(6)
    q1.metric("PENDING", qcounts.get("PENDING", 0))
    q2.metric("ARMED", qcounts.get("ARMED", 0))
    q3.metric("RUNNING", qcounts.get("RUNNING", 0))
    q4.metric("DONE", qcounts.get("DONE", 0))
    q5.metric("FAILED", qcounts.get("FAILED", 0))
    q6.metric("CANCELED", qcounts.get("CANCELED", 0))

    with st.form("queue_create_v2", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        symbol = c1.text_input("Symbol", value="ADA/USDT")
        panel_mode = c2.selectbox("Panel mode", ["CLOSE", "OPEN"], index=0)
        side = c3.selectbox("Side", ["SHORT", "LONG"], index=0)
        qty = c4.number_input("Qty (Cont)", min_value=0.0, value=50.0, step=1.0)

        c5, c6, c7, c8 = st.columns(4)
        order_kind = c5.selectbox(
            "Order kind",
            ["LIMIT", "POST_ONLY", "MARKET"],
            index=0,
        )
        limit_price = c6.number_input(
            "Limit price (USDT) (0 = none)",
            min_value=0.0,
            value=0.0,
            step=0.0001,
            format="%.6f",
        )
        reduce_only = c7.checkbox("Reduce-only", value=True)
        priority = c8.number_input("Priority (lower runs first)", min_value=0, value=100, step=1)

        c9, c10, c11 = st.columns(3)
        trigger_type = c9.selectbox("Trigger type", ["manual", "immediate", "price"], index=0)
        trigger_op = c10.selectbox("Trigger op", ["<=", ">="], index=0)
        trigger_price = c11.number_input(
            "Trigger price (0 = none)",
            min_value=0.0,
            value=0.0,
            step=0.0001,
            format="%.6f",
        )

        note = st.text_input("Note (optional)", value="")
        submit = st.form_submit_button("Create PENDING")

        if submit:
            if trigger_type == "price" and trigger_price <= 0:
                st.error("If trigger_type=price you must set trigger_price > 0.")
                st.stop()

            p = {
                "created_at": now_utc_iso(),
                "created_by": "streamlit",
                "exchange": "mexc",
                "market_type": "swap",
                "symbol": symbol.strip(),
                "intent": "close" if panel_mode == "CLOSE" else "open",
                "side": side,
                "reduce_only": 1 if reduce_only else 0,
                "qty": float(qty),
                "qty_unit": "contracts",
                "order_type": "limit" if order_kind in ("POST_ONLY", "LIMIT") else "market",
                "limit_price": float(limit_price) if limit_price > 0 else None,
                "trigger_type": trigger_type,
                "trigger_op": trigger_op if trigger_type == "price" else None,
                "trigger_price": float(trigger_price) if trigger_type == "price" else None,
                "status": "PENDING",
                "priority": int(priority),
                "note": note.strip() if note.strip() else None,
                "panel_mode": panel_mode,
                "order_kind": order_kind,
                "leverage": None,
                "last_update_at": now_utc_iso(),
            }

            inserted = queue_insert_v2(con, p)
            con.commit()
            if inserted == 1:
                st.success("Created PENDING action.")
            else:
                st.error("Insert failed (unexpected). Check logs.")

    st.divider()

    f1, f2, f3 = st.columns([1, 1, 1])
    status_filter = f1.selectbox(
        "Status filter",
        ["ALL", "PENDING", "ARMED", "RUNNING", "DONE", "FAILED", "CANCELED"],
        index=0,
    )
    limit = f2.number_input("Rows", min_value=20, value=200, step=20)
    if f3.button("Refresh table"):
        st.rerun()

    rows = queue_list_v2(con, status_filter, int(limit))
    if rows:
        dfq = pd.DataFrame(rows)
        cols = [
            "id",
            "created_at",
            "symbol",
            "panel_mode",
            "order_kind",
            "side",
            "qty",
            "limit_price",
            "trigger_type",
            "trigger_op",
            "trigger_price",
            "status",
            "priority",
            "attempts",
            "last_error",
            "last_update_at",
            "note",
        ]
        st.dataframe(dfq[[c for c in cols if c in dfq.columns]], width="stretch")

        st.write("### Controls")
        cA, cB, cC = st.columns(3)
        action_id = cA.number_input("Action ID", min_value=1, value=int(dfq["id"].iloc[0]), step=1)

        if cB.button("ARM selected (PENDING→ARMED)"):
            queue_arm(con, int(action_id))
            con.commit()
            st.success(f"ARMED {action_id}")
            st.rerun()

        if cC.button("CANCEL selected (PENDING/ARMED only)"):
            queue_cancel(con, int(action_id))
            con.commit()
            st.success(f"CANCELED {action_id}")
            st.rerun()

        cD, cE, cF = st.columns(3)

        if cD.button("Delete selected row"):
            n = delete_action_queue_row(con, int(action_id))
            if n:
                st.success(f"Deleted action_queue row id={int(action_id)}")
            else:
                st.warning("No row deleted.")
            st.rerun()

        if cE.button("Delete DONE/FAILED/CANCELED"):
            n = delete_action_queue_by_statuses(con, ["DONE", "FAILED", "CANCELED"])
            st.success(f"Deleted rows: {n}")
            st.rerun()

        if cF.button("Delete DONE only"):
            n = delete_action_queue_by_statuses(con, ["DONE"])
            st.success(f"Deleted DONE rows: {n}")
            st.rerun()

        st.caption("Tip: To clear RUNNING tasks, use the sidebar Emergency tools (Reset RUNNING).")
    else:
        st.info("No actions yet.")


# =================== Action Ledger ===================
with tab3:
    st.subheader("Action Ledger (manual notes / confirmations)")

    with st.form("add_action_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns(4)
        symbol_in = col1.text_input("Symbol", value="ADA/USDT")
        action_type_in = col2.selectbox("Action type", ["NOTE", "TRIM_LONG", "TRIM_SHORT", "HEDGE_OPEN", "HEDGE_CLOSE"])
        side_in = col3.selectbox("Side (optional)", ["", "LONG", "SHORT"])
        qty_in = col4.number_input("Qty (optional)", min_value=0.0, value=0.0, step=1.0)

        col5, col6 = st.columns(2)
        price_in = col5.number_input("Price (optional)", min_value=0.0, value=0.0, step=0.0001, format="%.6f")
        note_in = col6.text_input("Note (optional)", value="")

        submitted = st.form_submit_button("Add ledger entry")
        if submitted:
            add_action_ledger(
                con,
                created_at=now_utc_iso(),
                symbol=symbol_in.strip(),
                action_type=action_type_in,
                side=side_in if side_in else None,
                qty=qty_in if qty_in > 0 else None,
                price=price_in if price_in > 0 else None,
                note=note_in.strip() if note_in.strip() else None,
            )
            con.commit()
            st.success("Ledger entry saved.")
            st.rerun()

    actions = get_recent_actions(con, limit=50)
    if actions:
        df_actions = pd.DataFrame(actions)
        st.dataframe(df_actions, width="stretch")
    else:
        st.caption("No actions yet.")


# =================== Strategy (AUTO) ===================
with tab4:
    st.subheader("Strategy (AUTO) — LIMIT only")
    st.caption(
        "SAFE MODE keeps every action at 1 contract until the full logic is validated. "
        "Both OPEN and CLOSE now use LIMIT only."
    )

    try:
        from streamlit_autorefresh import st_autorefresh  # type: ignore
        autorefresh_available = True
    except Exception:
        autorefresh_available = False

    c1, c2, c3 = st.columns(3)
    sym = c1.text_input("Symbol (strategy)", value="ADA/USDT").strip()
    initial_side = c2.selectbox("Initial side", ["LONG", "SHORT"], index=1)
    priority = c3.number_input("Priority (lower runs first)", min_value=0, value=50, step=1)

    d1, d2, d3, d4 = st.columns(4)
    starting_capital = d1.number_input("Starting capital", min_value=1.0, value=100.0, step=10.0)
    secured_capital = d2.number_input("Secured capital", min_value=0.0, value=0.0, step=10.0)
    leverage = d3.number_input("Leverage for sizing / ROI math", min_value=1.0, value=500.0, step=1.0)
    dry = d4.checkbox("Dry-run (no enqueue)", value=False)

    e1, e2, e3, e4 = st.columns(4)
    hedge_loss_usdt = e1.number_input("Hedge trigger (uPnL <=)", value=-8.0, step=1.0)
    target_roi_pct = e2.number_input("Target ROI % per trim", min_value=1.0, value=200.0, step=10.0)
    limit_offset_pct = e3.number_input("CLOSE LIMIT offset %", min_value=0.0, value=0.05, step=0.01, format="%.4f")
    open_limit_offset_pct = e4.number_input("OPEN LIMIT offset %", min_value=0.0, value=0.05, step=0.01, format="%.4f")

    f1, f2, f3, f4 = st.columns(4)
    rebalance_trigger_pct = f1.number_input("Rebalance trigger %", min_value=0.0, value=0.10, step=0.01, format="%.4f")
    moderate_imbalance_ratio = f2.number_input("Moderate imbalance ratio", min_value=1.0, value=1.5, step=0.1)
    extreme_imbalance_ratio = f3.number_input("Extreme imbalance ratio", min_value=1.0, value=3.0, step=0.1)
    auto = f4.checkbox("Auto-run", value=False)

    g1, g2 = st.columns(2)
    safe_mode_one_contract = g1.checkbox("SAFE MODE: force 1 contract per action", value=True)
    contract_step = g2.number_input("Contract step", min_value=1.0, value=1.0, step=1.0)

    if autorefresh_available:
        r1, r2 = st.columns([1, 3])
        refresh_ms = r1.selectbox("Refresh interval", [2000, 3000, 5000, 8000], index=1)
        r2.caption("Auto-run uses autorefresh. If missing: pip install streamlit-autorefresh")
        if auto:
            st_autorefresh(interval=int(refresh_ms), key="strategy_autorefresh_v2_safe")
    else:
        st.warning("Auto-refresh not installed. For Auto-run: pip install streamlit-autorefresh. You can still use 'Run once'.")

    run_once = st.button("Run once (evaluate now)")

    if "strategy_last_action_ts_v2_safe" not in st.session_state:
        st.session_state.strategy_last_action_ts_v2_safe = 0.0
    if "strategy_log_v2_safe" not in st.session_state:
        st.session_state.strategy_log_v2_safe = []

    do_eval = run_once or auto

    if do_eval:
        acc = get_latest_account_snapshot(con)
        pos_ts, pos_rows = get_latest_positions_per_symbol_side(con)

        df_pos = pd.DataFrame(pos_rows) if pos_rows else pd.DataFrame(
            columns=["symbol", "side", "contracts", "entry_price", "unrealized_pnl"]
        )
        if not df_pos.empty:
            df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
            _to_float_series(df_pos, "contracts")
            _to_float_series(df_pos, "entry_price")
            _to_float_series(df_pos, "unrealized_pnl")

        L = get_leg(df_pos, sym, "LONG")
        S = get_leg(df_pos, sym, "SHORT")
        current_last_price = get_latest_ticker_price_for_symbol(con, sym)

        open_lot_rows = get_open_lots_for_symbol(con, sym)
        df_open_lots = pd.DataFrame(open_lot_rows) if open_lot_rows else pd.DataFrame()

        if not df_open_lots.empty:
            for col in ["qty_opened", "qty_remaining", "entry_price", "target_roi_pct", "leverage", "target_price"]:
                _to_float_series(df_open_lots, col)

        eligible_lots_df = compute_eligible_lots_df(df_open_lots, current_last_price) if not df_open_lots.empty else pd.DataFrame()

        eligible_open_lots_count = int(len(eligible_lots_df)) if not eligible_lots_df.empty else 0
        eligible_open_qty = float(eligible_lots_df["qty_remaining"].sum()) if not eligible_lots_df.empty else 0.0

        next_unreached_target_price = None
        if not df_open_lots.empty:
            unreached_df = df_open_lots.copy()

            if current_last_price is not None and current_last_price > 0:
                def _not_reached_yet(row):
                    side = str(row.get("side") or "").upper()
                    target_price = float(row.get("target_price") or 0.0)
                    qty_remaining = float(row.get("qty_remaining") or 0.0)
                    if qty_remaining <= 0 or target_price <= 0:
                        return False
                    if side == "LONG":
                        return float(current_last_price) < target_price
                    if side == "SHORT":
                        return float(current_last_price) > target_price
                    return False

                unreached_df = unreached_df[unreached_df.apply(_not_reached_yet, axis=1)].copy()

            if not unreached_df.empty:
                if current_last_price is not None and current_last_price > 0:
                    unreached_df["distance_to_target_abs"] = (unreached_df["target_price"] - float(current_last_price)).abs()
                    unreached_df = unreached_df.sort_values(["distance_to_target_abs", "id"], ascending=[True, True])
                else:
                    unreached_df = unreached_df.sort_values(["id"], ascending=[True])

                next_unreached_target_price = float(unreached_df.iloc[0]["target_price"])

        total_equity = float((acc or {}).get("equity") or 0.0)
        active_strategy_capital = max(0.0, total_equity - float(secured_capital))
        regime_params = get_strategy_regime_params(float(starting_capital), float(secured_capital))

        sizing = compute_strategy_sizing(
            active_strategy_capital=active_strategy_capital,
            last_price=current_last_price,
            leverage=float(leverage),
            long_contracts=float(L["contracts"] or 0.0),
            short_contracts=float(S["contracts"] or 0.0),
            initial_entry_pct=float(regime_params["initial_entry_pct"]),
            rebalance_step_pct=float(regime_params["rebalance_step_pct"]),
            max_symbol_envelope_pct=float(regime_params["max_symbol_envelope_pct"]),
            max_action_contracts_cap=float(regime_params["max_action_contracts_cap"]),
            min_action_contracts=1.0,
            contract_step=float(contract_step),
        )

        if safe_mode_one_contract:
            if sizing["final_initial_contracts"] >= 1:
                sizing["final_initial_contracts"] = 1.0
            if sizing["final_rebalance_contracts"] >= 1:
                sizing["final_rebalance_contracts"] = 1.0

        target_move_pct = float(target_roi_pct) / float(leverage) / 100.0

        long_entry = float(L["entry_price"] or 0.0)
        short_entry = float(S["entry_price"] or 0.0)
        long_upnl = float(L["unrealized_pnl"] or 0.0)
        short_upnl = float(S["unrealized_pnl"] or 0.0)
        long_contracts = float(L["contracts"] or 0.0)
        short_contracts = float(S["contracts"] or 0.0)

        long_target_price = long_entry * (1.0 + target_move_pct) if long_entry > 0 else None
        short_target_price = short_entry * (1.0 - target_move_pct) if short_entry > 0 else None

        long_ready = (
            current_last_price is not None
            and long_target_price is not None
            and current_last_price >= long_target_price
        )
        short_ready = (
            current_last_price is not None
            and short_target_price is not None
            and current_last_price <= short_target_price
        )

        imbalance_ratio = compute_imbalance_ratio(long_contracts, short_contracts)
        if long_contracts > short_contracts:
            bigger_side = "LONG"
            smaller_side = "SHORT"
        elif short_contracts > long_contracts:
            bigger_side = "SHORT"
            smaller_side = "LONG"
        else:
            bigger_side = ""
            smaller_side = ""

        valid_rebalance_signal = (
            min(long_contracts, short_contracts) > 0
            and has_valid_rebalance_signal(
                smaller_side=smaller_side,
                last_price=current_last_price,
                long_entry=long_entry,
                short_entry=short_entry,
                rebalance_trigger_pct=float(rebalance_trigger_pct),
            )
        )

        st.write("Latest snapshot:")
        st.json({
            "account_ts": (acc or {}).get("created_at"),
            "positions_ts": pos_ts,
            "total_equity": total_equity,
            "secured_capital": float(secured_capital),
            "active_strategy_capital": active_strategy_capital,
        })

        st.write("Regime:")
        st.json(regime_params)

        st.write("Legs:")
        st.json({"LONG": L, "SHORT": S})

        st.write("Market:")
        st.json({"last_price": current_last_price})

        st.write("Sizing:")
        st.json(sizing)

        st.write("Derived targets:")
        st.json({
            "target_roi_pct": float(target_roi_pct),
            "leverage": float(leverage),
            "target_move_pct": target_move_pct,
            "long_target_price": long_target_price,
            "short_target_price": short_target_price,
            "safe_mode_one_contract": bool(safe_mode_one_contract),
        })

        st.write("Imbalance:")
        st.json({
            "imbalance_ratio": imbalance_ratio,
            "bigger_side": bigger_side,
            "smaller_side": smaller_side,
            "valid_rebalance_signal": valid_rebalance_signal,
            "moderate_threshold": float(moderate_imbalance_ratio),
            "extreme_threshold": float(extreme_imbalance_ratio),
        })

        guards = {
            "active_queue_task": has_active_task_for_symbol(con, sym),
            "active_pending_limit": has_active_pending_limit_for_symbol(con, sym),
            "active_pending_chase": has_active_pending_chase_for_symbol(con, sym),
        }
        st.write("Guards:")
        st.json(guards)

        st.write("Lot debug:")
        st.json({
            "open_lots_count": int(len(df_open_lots)) if not df_open_lots.empty else 0,
            "eligible_open_lots_count": eligible_open_lots_count,
            "eligible_open_qty": eligible_open_qty,
            "next_unreached_target_price": next_unreached_target_price,
        })

        lot_trim_ready = eligible_open_lots_count > 0
        leg_trim_ready = long_ready or short_ready

        if not eligible_lots_df.empty:
            st.write("Eligible open lots now:")
            eligible_cols = [
                "id",
                "symbol",
                "side",
                "qty_remaining",
                "entry_price",
                "target_price",
                "opened_at",
                "source_task_type",
                "source_task_id",
                "status",
            ]
            eligible_cols = [c for c in eligible_cols if c in eligible_lots_df.columns]
            st.dataframe(eligible_lots_df[eligible_cols], width="stretch", hide_index=True)

        now = time.time()
        cooldown_left = max(
            0.0,
            float(regime_params["cooldown_sec"]) - (now - float(st.session_state.strategy_last_action_ts_v2_safe)),
        )

        decision = None
        strategy_state = "UNDEFINED"
        action_reason = ""
        no_action_reason = ""

        if cooldown_left > 0:
            strategy_state = "COOLDOWN"
            no_action_reason = f"COOLDOWN_ACTIVE ({cooldown_left:.1f}s remaining)"
            st.info(f"Cooldown active: {cooldown_left:.1f}s remaining.")

        elif guards["active_queue_task"]:
            strategy_state = "BLOCKED"
            no_action_reason = "BLOCKED_BY_ACTIVE_QUEUE_TASK"
            st.warning("Guard: ARMED/RUNNING task exists for this symbol. Waiting.")

        elif guards["active_pending_limit"]:
            strategy_state = "BLOCKED"
            no_action_reason = "BLOCKED_BY_PENDING_LIMIT"
            st.warning("Guard: PENDING/TRIGGERED LIMIT follow-up exists for this symbol. Waiting.")

        elif guards["active_pending_chase"]:
            strategy_state = "BLOCKED"
            no_action_reason = "BLOCKED_BY_PENDING_CHASE"
            st.warning("Guard: legacy PENDING CHASE exists for this symbol. Waiting.")

        else:
            side_to_open = initial_side
            hedge_loss_triggered = False

            if long_contracts > 0 and short_contracts <= 0:
                side_to_open = "SHORT"
                hedge_loss_triggered = long_upnl <= float(hedge_loss_usdt)
            elif short_contracts > 0 and long_contracts <= 0:
                side_to_open = "LONG"
                hedge_loss_triggered = short_upnl <= float(hedge_loss_usdt)
            elif long_contracts > 0 and short_contracts > 0:
                side_to_open = smaller_side if smaller_side else initial_side

            open_class = classify_open_action(
                long_contracts=long_contracts,
                short_contracts=short_contracts,
                side_to_open=side_to_open,
                regime=regime_params["regime"],
                hedge_loss_triggered=hedge_loss_triggered,
                valid_rebalance_signal=valid_rebalance_signal,
                imbalance_ratio=imbalance_ratio,
                moderate_imbalance_ratio=float(moderate_imbalance_ratio),
                extreme_imbalance_ratio=float(extreme_imbalance_ratio),
            )

            close_class = classify_close_action(
                lot_trim_ready=lot_trim_ready,
                leg_trim_ready=leg_trim_ready,
                imbalance_ratio=imbalance_ratio,
                moderate_imbalance_ratio=float(moderate_imbalance_ratio),
                extreme_imbalance_ratio=float(extreme_imbalance_ratio),
            )

            if long_contracts <= 0 and short_contracts <= 0:
                strategy_state = "NO_POSITION"
                if open_class == "INITIAL" and sizing["final_initial_contracts"] >= 1:
                    open_limit_price = build_open_limit_price(
                        side=initial_side,
                        current_last_price=current_last_price,
                        open_limit_offset_pct=float(open_limit_offset_pct),
                    )
                    if open_limit_price is not None:
                        decision = (
                            "OPEN_LIMIT",
                            initial_side,
                            float(sizing["final_initial_contracts"]),
                            float(open_limit_price),
                            f"auto: initial {initial_side} | regime={regime_params['regime']} | class={open_class}",
                        )
                        action_reason = f"INITIAL_ENTRY_{initial_side}"
                    else:
                        no_action_reason = "INITIAL_ENTRY_NO_MARKET_PRICE"
                else:
                    no_action_reason = "INITIAL_ENTRY_SIZE_BELOW_MIN"

            elif long_contracts > 0 and short_contracts <= 0:
                strategy_state = "ONE_SIDED_LONG"
                if open_class == "HEDGE" and sizing["final_rebalance_contracts"] >= 1:
                    open_limit_price = build_open_limit_price(
                        side="SHORT",
                        current_last_price=current_last_price,
                        open_limit_offset_pct=float(open_limit_offset_pct),
                    )
                    if open_limit_price is not None:
                        decision = (
                            "OPEN_LIMIT",
                            "SHORT",
                            float(sizing["final_rebalance_contracts"]),
                            float(open_limit_price),
                            f"auto: hedge SHORT | regime={regime_params['regime']} | class={open_class}",
                        )
                        action_reason = "ONE_SIDED_HEDGE_TRIGGER_SHORT"
                    else:
                        no_action_reason = "ONE_SIDED_LONG_NO_MARKET_PRICE"
                else:
                    no_action_reason = "ONE_SIDED_LONG_NO_HEDGE_TRIGGER"

            elif short_contracts > 0 and long_contracts <= 0:
                strategy_state = "ONE_SIDED_SHORT"
                if open_class == "HEDGE" and sizing["final_rebalance_contracts"] >= 1:
                    open_limit_price = build_open_limit_price(
                        side="LONG",
                        current_last_price=current_last_price,
                        open_limit_offset_pct=float(open_limit_offset_pct),
                    )
                    if open_limit_price is not None:
                        decision = (
                            "OPEN_LIMIT",
                            "LONG",
                            float(sizing["final_rebalance_contracts"]),
                            float(open_limit_price),
                            f"auto: hedge LONG | regime={regime_params['regime']} | class={open_class}",
                        )
                        action_reason = "ONE_SIDED_HEDGE_TRIGGER_LONG"
                    else:
                        no_action_reason = "ONE_SIDED_SHORT_NO_MARKET_PRICE"
                else:
                    no_action_reason = "ONE_SIDED_SHORT_NO_HEDGE_TRIGGER"

            elif long_contracts > 0 and short_contracts > 0:
                roi_trim_ready = leg_trim_ready or lot_trim_ready

                if close_class in ("LOT_TRIM", "LEG_TRIM", "HARVEST", "DE_RISK") and roi_trim_ready:
                    winner_side = get_winner_side_from_lots_or_leg(
                        lot_trim_ready=lot_trim_ready,
                        eligible_lots_df=eligible_lots_df,
                        long_ready=long_ready,
                        short_ready=short_ready,
                        long_upnl=long_upnl,
                        short_upnl=short_upnl,
                    )

                    reason_suffix = (
                        f"{close_class.lower()} | lot-eligible"
                        if lot_trim_ready
                        else f"{close_class.lower()} | leg-target"
                    )

                    decision = build_trim_decision(
                        winner_side=winner_side,
                        long_contracts=long_contracts,
                        short_contracts=short_contracts,
                        long_target_price=long_target_price,
                        short_target_price=short_target_price,
                        trim_winner_pct=float(regime_params["trim_winner_pct"]),
                        limit_offset_pct=float(limit_offset_pct),
                        safe_mode_one_contract=bool(safe_mode_one_contract),
                        contract_step=float(contract_step),
                        reason_suffix=reason_suffix,
                    )

                    if decision:
                        strategy_state = f"HEDGED_{close_class}"
                        action_reason = f"{close_class}_TRIM_{winner_side}"
                    else:
                        strategy_state = f"HEDGED_{close_class}"
                        no_action_reason = f"{close_class}_TRIM_SIZE_BELOW_MIN"

                elif open_class in ("REBALANCE", "RECOVERY_ADD") and sizing["final_rebalance_contracts"] >= 1:
                    side_for_open = smaller_side if smaller_side else side_to_open
                    open_limit_price = build_open_limit_price(
                        side=side_for_open,
                        current_last_price=current_last_price,
                        open_limit_offset_pct=float(open_limit_offset_pct),
                    )
                    if open_limit_price is not None:
                        decision = (
                            "OPEN_LIMIT",
                            side_for_open,
                            float(sizing["final_rebalance_contracts"]),
                            float(open_limit_price),
                            f"auto: {open_class.lower()} {side_for_open} | regime={regime_params['regime']} | ratio={imbalance_ratio:.3f}",
                        )
                        strategy_state = f"HEDGED_{open_class}"
                        action_reason = f"{open_class}_{side_for_open}"
                    else:
                        strategy_state = f"HEDGED_{open_class}"
                        no_action_reason = f"{open_class}_NO_MARKET_PRICE"
                else:
                    strategy_state = "HEDGED_WAIT"
                    no_action_reason = "NO_OPEN_OR_CLOSE_SIGNAL"

            else:
                strategy_state = "UNKNOWN"
                no_action_reason = "UNKNOWN_STATE"

        st.write("Strategy state:")
        st.json({
            "strategy_state": strategy_state,
            "action_reason": action_reason if action_reason else None,
            "no_action_reason": no_action_reason if no_action_reason else None,
        })

        st.write("Decision:", decision or "—")

        if decision:
            if decision[0] == "OPEN_LIMIT":
                _, side, qty, limit_price, note = decision

                if dry:
                    st.warning(f"DRY-RUN: would enqueue ARMED OPEN+LIMIT qty={qty:.0f} @ {limit_price:.6f}.")
                else:
                    try:
                        enqueue_armed_open_limit(
                            con,
                            symbol=sym,
                            side=side,
                            qty=float(qty),
                            limit_price=float(limit_price),
                            note=note,
                            priority=int(priority),
                        )
                        st.session_state.strategy_last_action_ts_v2_safe = time.time()
                        st.success(f"Enqueued ARMED action (OPEN+LIMIT) qty={qty:.0f} @ {limit_price:.6f}.")
                    except Exception as e:
                        st.error(f"Enqueue failed: {e}")

            elif decision[0] == "CLOSE_LIMIT":
                _, side, qty, limit_price, note = decision

                if dry:
                    st.warning(f"DRY-RUN: would enqueue ARMED CLOSE+LIMIT qty={qty:.0f} @ {limit_price:.6f}.")
                else:
                    try:
                        enqueue_armed_close_limit(
                            con,
                            symbol=sym,
                            side=side,
                            qty=float(qty),
                            limit_price=float(limit_price),
                            note=note,
                            priority=int(priority),
                        )
                        st.session_state.strategy_last_action_ts_v2_safe = time.time()
                        st.success(f"Enqueued ARMED action (CLOSE+LIMIT) qty={qty:.0f} @ {limit_price:.6f}.")
                    except Exception as e:
                        st.error(f"Enqueue failed: {e}")

            st.session_state.strategy_log_v2_safe.insert(
                0,
                {
                    "ts": now_utc_iso(),
                    "symbol": sym,
                    "regime": regime_params["regime"],
                    "strategy_state": strategy_state,
                    "action_reason": action_reason,
                    "no_action_reason": no_action_reason,
                    "decision": str(decision),
                    "dry": bool(dry),
                    "LONG": L,
                    "SHORT": S,
                    "last_price": current_last_price,
                    "active_strategy_capital": active_strategy_capital,
                    "imbalance_ratio": imbalance_ratio,
                    "safe_mode_one_contract": bool(safe_mode_one_contract),
                    "lot_trim_ready": lot_trim_ready,
                    "leg_trim_ready": leg_trim_ready,
                    "open_class": open_class if 'open_class' in locals() else None,
                    "close_class": close_class if 'close_class' in locals() else None,
                },
            )
            st.session_state.strategy_log_v2_safe = st.session_state.strategy_log_v2_safe[:50]

    if st.session_state.get("strategy_log_v2_safe"):
        st.write("### Strategy log (last 50)")
        st.dataframe(pd.DataFrame(st.session_state.strategy_log_v2_safe), width="stretch")


# =================== Pending LIMIT ===================
with tab5:
    st.subheader("Pending LIMIT tasks")
    counts = get_pending_limit_status_counts(con)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("PENDING", counts.get("PENDING", 0))
    m2.metric("TRIGGERED", counts.get("TRIGGERED", 0))
    m3.metric("FILLED", counts.get("FILLED", 0))
    m4.metric("EXPIRED", counts.get("EXPIRED", 0))
    m5.metric("FAILED_AFTER_TRIGGER", counts.get("FAILED_AFTER_TRIGGER", 0))

    status_options = [
        "ALL",
        "PENDING",
        "TRIGGERED",
        "FILLED",
        "EXPIRED",
        "FAILED_AFTER_TRIGGER",
    ]

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        pending_status_filter = st.selectbox(
            "Status filter",
            status_options,
            index=0,
            key="pending_status_filter",
        )

    with col2:
        pending_limit_rows = list_pending_limit_tasks(con, status=pending_status_filter, limit=200)
        pending_ids = [int(r["id"]) for r in pending_limit_rows] if pending_limit_rows else []
        selected_task_id = st.selectbox(
            "Select task id",
            pending_ids if pending_ids else [0],
            key="selected_pending_limit_task_id",
        )

    with col3:
        if st.button("Refresh pending LIMIT tasks", key="refresh_pending_limit_tasks"):
            st.rerun()

    col4, col5, col6 = st.columns([1, 1, 1])

    with col4:
        if st.button("Reset selected task", key="reset_selected_pending_limit_task"):
            if selected_task_id:
                n = reset_pending_limit_task(con, int(selected_task_id))
                if n:
                    st.success(f"Reset task id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col5:
        if st.button("Set created_at=now", key="set_created_now_pending_limit_task"):
            if selected_task_id:
                n = set_pending_limit_created_now(con, int(selected_task_id))
                if n:
                    st.success(f"Updated created_at for task id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col6:
        if st.button("Delete resolved tasks", key="delete_resolved_pending_limit_tasks"):
            n = delete_resolved_pending_limit_tasks(con)
            st.success(f"Deleted resolved tasks: {n}")
            st.rerun()

    col7, col8, col9 = st.columns([1, 1, 1])

    with col7:
        if st.button("Mark selected FILLED", key="mark_selected_pending_limit_filled"):
            if selected_task_id:
                n = mark_pending_limit_task_status(
                    con,
                    int(selected_task_id),
                    "FILLED",
                    " | manual_mark_filled",
                )
                if n:
                    st.success(f"Marked FILLED: id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col8:
        if st.button("Mark selected FAILED", key="mark_selected_pending_limit_failed"):
            if selected_task_id:
                n = mark_pending_limit_task_status(
                    con,
                    int(selected_task_id),
                    "FAILED_AFTER_TRIGGER",
                    " | manual_mark_failed",
                )
                if n:
                    st.success(f"Marked FAILED_AFTER_TRIGGER: id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col9:
        if st.button("Delete selected task", key="delete_selected_pending_limit_task"):
            if selected_task_id:
                n = delete_pending_limit_task(con, int(selected_task_id))
                if n:
                    st.success(f"Deleted task id={int(selected_task_id)}")
                else:
                    st.warning("No task deleted")
                st.rerun()

    if pending_limit_rows:
        df_pending = pd.DataFrame([dict(r) for r in pending_limit_rows])

        display_cols = [
            "id",
            "action_id",
            "symbol",
            "side",
            "panel_mode",
            "limit_price",
            "qty",
            "status",
            "trigger_seen",
            "baseline_contracts",
            "triggered_at",
            "attempt_count",
            "created_at",
            "updated_at",
            "resolved_at",
            "note",
        ]
        display_cols = [c for c in display_cols if c in df_pending.columns]

        st.dataframe(
            df_pending[display_cols],
            width="stretch",
            hide_index=True,
        )

        if selected_task_id:
            selected_row = df_pending[df_pending["id"] == selected_task_id]
            if not selected_row.empty:
                st.markdown("**Selected task details**")
                st.json(selected_row.iloc[0].to_dict())
    else:
        st.info("No pending_limit_tasks rows for the selected filter.")


# =================== Pending CHASE ===================
with tab6:
    st.subheader("Pending CHASE tasks (legacy)")
    st.caption("This tab is legacy/read-only support. New strategy flow should not create CHASE tasks anymore.")

    counts = get_pending_chase_status_counts(con)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("PENDING", counts.get("PENDING", 0))
    m2.metric("FILLED", counts.get("FILLED", 0))
    m3.metric("EXPIRED", counts.get("EXPIRED", 0))
    m4.metric("FAILED", counts.get("FAILED", 0))

    status_options = [
        "ALL",
        "PENDING",
        "FILLED",
        "EXPIRED",
        "FAILED",
    ]

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        pending_chase_status_filter = st.selectbox(
            "Status filter",
            status_options,
            index=0,
            key="pending_chase_status_filter",
        )

    with col2:
        pending_chase_rows = list_pending_chase_tasks(con, status=pending_chase_status_filter, limit=200)
        pending_chase_ids = [int(r["id"]) for r in pending_chase_rows] if pending_chase_rows else []
        selected_chase_task_id = st.selectbox(
            "Select task id",
            pending_chase_ids if pending_chase_ids else [0],
            key="selected_pending_chase_task_id",
        )

    with col3:
        if st.button("Refresh pending CHASE tasks", key="refresh_pending_chase_tasks"):
            st.rerun()

    col4, col5, col6 = st.columns([1, 1, 1])

    with col4:
        if st.button("Reset selected CHASE", key="reset_selected_pending_chase_task"):
            if selected_chase_task_id:
                n = reset_pending_chase_task(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Reset CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col5:
        if st.button("Set CHASE created_at=now", key="set_created_now_pending_chase_task"):
            if selected_chase_task_id:
                n = set_pending_chase_created_now(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Updated created_at for CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col6:
        if st.button("Delete resolved CHASE", key="delete_resolved_pending_chase_tasks"):
            n = delete_resolved_pending_chase_tasks(con)
            st.success(f"Deleted resolved CHASE tasks: {n}")
            st.rerun()

    col7, col8, col9 = st.columns([1, 1, 1])

    with col7:
        if st.button("Mark selected CHASE FILLED", key="mark_selected_pending_chase_filled"):
            if selected_chase_task_id:
                n = mark_pending_chase_task_status(
                    con,
                    int(selected_chase_task_id),
                    "FILLED",
                    " | manual_mark_filled",
                )
                if n:
                    st.success(f"Marked CHASE FILLED: id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col8:
        if st.button("Mark selected CHASE FAILED", key="mark_selected_pending_chase_failed"):
            if selected_chase_task_id:
                n = mark_pending_chase_task_status(
                    con,
                    int(selected_chase_task_id),
                    "FAILED",
                    " | manual_mark_failed",
                )
                if n:
                    st.success(f"Marked CHASE FAILED: id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col9:
        if st.button("Delete selected CHASE", key="delete_selected_pending_chase_task"):
            if selected_chase_task_id:
                n = delete_pending_chase_task(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Deleted CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task deleted")
                st.rerun()

    if pending_chase_rows:
        df_pending_chase = pd.DataFrame([dict(r) for r in pending_chase_rows])

        display_cols = [
            "id",
            "action_id",
            "symbol",
            "side",
            "panel_mode",
            "qty",
            "status",
            "baseline_contracts",
            "filled_contracts",
            "created_at",
            "updated_at",
            "resolved_at",
            "note",
        ]
        display_cols = [c for c in display_cols if c in df_pending_chase.columns]

        st.dataframe(
            df_pending_chase[display_cols],
            width="stretch",
            hide_index=True,
        )

        if selected_chase_task_id:
            selected_row = df_pending_chase[df_pending_chase["id"] == selected_chase_task_id]
            if not selected_row.empty:
                st.markdown("**Selected CHASE task details**")
                st.json(selected_row.iloc[0].to_dict())
    else:
        st.info("No pending_chase_tasks rows for the selected filter.")


# =================== Lots ===================
with tab7:
    st.subheader("Lots")
    st.caption("View open lots, realized lots, and which lots are eligible for trim at the current market price.")

    lot_symbols = get_distinct_lot_symbols(con)
    lot_symbol_filter = st.selectbox(
        "Symbol filter",
        ["ALL"] + lot_symbols if lot_symbols else ["ALL"],
        index=0,
        key="lots_symbol_filter",
    )

    lot_status_filter = st.selectbox(
        "Open lots status",
        ["ALL", "OPEN", "CLOSED"],
        index=0,
        key="lots_status_filter",
    )

    lots_limit = st.number_input("Rows per table", min_value=20, value=200, step=20, key="lots_rows_limit")

    if st.button("Refresh lots"):
        st.rerun()

    lots_rows = list_position_lots(con, symbol=lot_symbol_filter, status=lot_status_filter, limit=int(lots_limit))
    realizations_rows = list_lot_realizations(con, symbol=lot_symbol_filter, limit=int(lots_limit))

    df_lots = pd.DataFrame(lots_rows) if lots_rows else pd.DataFrame()
    df_real = pd.DataFrame(realizations_rows) if realizations_rows else pd.DataFrame()

    current_lot_price = None
    if lot_symbol_filter != "ALL":
        current_lot_price = get_latest_ticker_price_for_symbol(con, lot_symbol_filter)

    top1, top2, top3 = st.columns(3)
    top1.metric("Open lot rows", int(len(df_lots[df_lots["status"] == "OPEN"])) if not df_lots.empty and "status" in df_lots.columns else 0)
    top2.metric("Realizations", int(len(df_real)) if not df_real.empty else 0)
    top3.metric("Current price", f"{current_lot_price:.6f}" if current_lot_price is not None else "—")

    if not df_lots.empty:
        for col in ["qty_opened", "qty_remaining", "entry_price", "target_roi_pct", "leverage", "target_price"]:
            _to_float_series(df_lots, col)

        st.write("### position_lots")
        lot_cols = [
            "id",
            "symbol",
            "side",
            "qty_opened",
            "qty_remaining",
            "entry_price",
            "target_price",
            "target_roi_pct",
            "leverage",
            "opened_at",
            "source_action_id",
            "source_task_type",
            "source_task_id",
            "status",
        ]
        lot_cols = [c for c in lot_cols if c in df_lots.columns]
        st.dataframe(df_lots[lot_cols], width="stretch", hide_index=True)

        if lot_symbol_filter != "ALL":
            eligible_df = compute_eligible_lots_df(df_lots, current_lot_price)
            st.write("### Eligible now")
            if not eligible_df.empty:
                eligible_qty = float(eligible_df["qty_remaining"].sum()) if "qty_remaining" in eligible_df.columns else 0.0
                st.caption(f"Eligible qty total: {eligible_qty:.4f}")
                eligible_cols = [
                    "id",
                    "symbol",
                    "side",
                    "qty_remaining",
                    "entry_price",
                    "target_price",
                    "opened_at",
                    "source_task_type",
                    "source_task_id",
                    "status",
                ]
                eligible_cols = [c for c in eligible_cols if c in eligible_df.columns]
                st.dataframe(eligible_df[eligible_cols], width="stretch", hide_index=True)
            else:
                st.info("No eligible lots at the current price.")
        else:
            st.caption("Choose a specific symbol to see which open lots are eligible right now.")
    else:
        st.info("No rows in position_lots for the selected filter.")

    if not df_real.empty:
        for col in ["close_qty", "entry_price", "close_price", "target_price", "realized_roi_pct"]:
            _to_float_series(df_real, col)

        st.write("### lot_realizations")
        real_cols = [
            "id",
            "lot_id",
            "symbol",
            "side",
            "close_qty",
            "entry_price",
            "close_price",
            "target_price",
            "realized_roi_pct",
            "closed_at",
            "close_action_id",
            "close_task_type",
            "close_task_id",
            "note",
        ]
        real_cols = [c for c in real_cols if c in df_real.columns]
        st.dataframe(df_real[real_cols], width="stretch", hide_index=True)
    else:
        st.info("No rows in lot_realizations for the selected filter.")

con.close()
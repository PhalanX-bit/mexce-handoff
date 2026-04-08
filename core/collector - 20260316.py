# core/collector.py
# Read-only collector v0.1 (MEXC futures/swap) + RECONCILE:
# - Writes account_snapshot + positions_snapshot (+ optional fills) into SQLite
# - Normalizes symbols like "ADA/USDT:USDT" -> "ADA/USDT"
# - Fetches positions via ccxt.fetch_positions()
# - IMPORTANT: Reconciles "missing" positions:
#   If a position was previously open in DB, but is not returned anymore by the exchange,
#   we insert a synthetic snapshot row with contracts=0 for the current snapshot_ts.
#   This prevents "ghost open positions" in downstream UI/state logic.
#
# Requirements:
#   python -m pip install ccxt python-dotenv
#
# .env in project root (mexce/.env):
#   MEXC_API_KEY=...
#   MEXC_API_SECRET=...

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import ccxt
from dotenv import load_dotenv

from core.db import connect

# Turn on only when you really need it (MEXC often requires symbol and can rate-limit)
ENABLE_FILLS = True

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
    # "ADA/USDT:USDT" -> "ADA/USDT"
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
                "defaultType": "swap",  # futures (perpetual)
            },
        }
    )


def normalize_ccxt_positions(raw_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize ccxt positions to a stable schema:
    - symbol: "ADA/USDT"
    - side: "LONG" | "SHORT"
    - contracts: float
    - entryPrice: float
    - unrealizedPnl: float

    Note:
    We keep only "meaningful" open positions here.
    Closures are handled by DB reconcile (synthetic contracts=0 rows).
    """
    out: List[Dict[str, Any]] = []
    for p in raw_positions:
        sym = base_symbol(p.get("symbol"))

        side_raw = p.get("side")  # often "long"/"short"
        side = None
        if str(side_raw).lower() == "long":
            side = "LONG"
        elif str(side_raw).lower() == "short":
            side = "SHORT"

        contracts = safe_float(p.get("contracts"), 0.0) or 0.0
        entry = safe_float(p.get("entryPrice"), 0.0) or 0.0
        upnl = safe_float(p.get("unrealizedPnl"), 0.0) or 0.0

        # Keep only actually-open positions from exchange
        # (some exchanges return placeholders with 0 contracts)
        if contracts == 0.0:
            continue

        out.append(
            {
                "symbol": sym,
                "side": side or "LONG",  # default if missing
                "contracts": contracts,
                "entryPrice": entry,
                "unrealizedPnl": upnl,
            }
        )
    return out


def _pos_key(symbol: Optional[str], side: Optional[str]) -> Tuple[str, str]:
    return (symbol or "", side or "")


def get_latest_open_positions_from_db(con) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Returns latest known positions per (symbol, side) from DB WHERE latest contracts > 0.
    This is used to detect positions that disappeared from exchange => mark as closed via
    synthetic snapshot row contracts=0 for current snapshot_ts.
    """
    # Assumption: created_at is ISO string => MAX(created_at) works lexicographically.
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


def collect() -> None:
    ex = make_exchange()
    try:
        ex.load_markets()
    except Exception as e:
        print("load_markets failed (non-fatal):", e)

    con = connect()

    # One timestamp for the whole run (snapshot)
    snapshot_ts = now_utc_iso()

    # ---- Balance / margin (best-effort) ----
    equity = free = mr = None
    try:
        bal = ex.fetch_balance()
        equity = bal.get("total", {}).get("USDT")
        free = bal.get("free", {}).get("USDT")

        # margin ratio is not always present
        info = bal.get("info", {}) if isinstance(bal, dict) else {}
        mr_val = info.get("marginRatio")
        mr = safe_float(mr_val, None) if mr_val is not None else None
    except Exception as e:
        print("fetch_balance failed:", e)

    con.execute(
        "INSERT INTO account_snapshot (created_at, equity, free_margin, margin_ratio) VALUES (?, ?, ?, ?)",
        (snapshot_ts, equity, free, mr),
    )

    # ---- Positions ----
    # 1) Read latest OPEN positions from DB (before we fetch live)
    prev_open = {}
    try:
        prev_open = get_latest_open_positions_from_db(con)
    except Exception as e:
        # If table is empty or query fails, continue without reconcile
        print("prev_open query failed (non-fatal):", e)
        prev_open = {}

    # 2) Fetch live positions
    raw_positions: List[Dict[str, Any]] = []
    try:
        raw_positions = ex.fetch_positions()
    except Exception as e:
        print("fetch_positions failed:", e)

    positions = normalize_ccxt_positions(raw_positions)
    # ---- Tickers (current price) for symbols we participate in ----
    watch_symbols: Set[str] = {p["symbol"] for p in positions if p.get("symbol")}

    for sym in sorted(watch_symbols):
        try:
            t = ex.fetch_ticker(sym)

            # Normalized ccxt field
            last = safe_float(t.get("last"), None)

            # Mark price is exchange-specific; sometimes inside info
            info = t.get("info", {}) if isinstance(t, dict) else {}
            mark = safe_float(info.get("markPrice") or info.get("mark_price") or info.get("mark"), None)

            insert_ticker_snapshot(con, snapshot_ts, sym, last, mark)
        except Exception as e:
            print(f"fetch_ticker failed for {sym}:", e)
            continue

    # 3) Insert live positions for this snapshot
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

    # 4) RECONCILE: if something was previously open, but missing now -> insert synthetic close row
    #    This is the key fix for "position closed days ago but still shown as open" in downstream state.
    try:
        missing = [k for k in prev_open.keys() if k not in live_keys]
        for (sym, side) in missing:
            prev = prev_open[(sym, side)]
            # Insert a "closed" snapshot row for current timestamp
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

    # ---- Recent fills (optional; MEXC requires symbol; use symbols from live positions) ----
    if ENABLE_FILLS:
        try:
            symbols: Set[str] = set(watch_symbols)
            for sym in sorted(symbols):
                # ccxt expects the full symbol; for swaps MEXC typically accepts "ADA/USDT"
                try:
                    trades = ex.fetch_my_trades(sym, limit=50)
                except Exception as e:
                    print(f"fills fetch skipped for {sym}:", e)
                    continue

                for t in trades:
                    fee_cost = None
                    if isinstance(t.get("fee"), dict):
                        fee_cost = t["fee"].get("cost")

                    con.execute(
                        """
                        INSERT OR IGNORE INTO fills
                        (exchange_trade_id, created_at, symbol, side, price, amount, fee)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            t.get("id"),
                            t.get("datetime"),
                            base_symbol(t.get("symbol")),
                            t.get("side"),
                            safe_float(t.get("price"), 0.0),
                            safe_float(t.get("amount"), 0.0),
                            safe_float(fee_cost, 0.0) if fee_cost is not None else None,
                        ),
                    )
        except Exception as e:
            print("fills fetch skipped:", e)

    con.commit()
    con.close()
    print("Collector run OK")


if __name__ == "__main__":
    collect()

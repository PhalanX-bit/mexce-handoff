# core/collector.py
# Read-only collector v0 (MEXC futures/swap):
# - Writes account_snapshot + positions_snapshot + fills into SQLite
# - Normalizes symbols like "ADA/USDT:USDT" -> "ADA/USDT"
# - Fetches positions via ccxt.fetch_positions()
# - Fetches fills per-symbol (MEXC requires symbol argument)
#
# Requirements:
#   python -m pip install ccxt python-dotenv
#
# .env in project root (mexce/.env):
#   MEXC_API_KEY=...
#   MEXC_API_SECRET=...

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import ccxt
from dotenv import load_dotenv

from core.db import connect

ENABLE_FILLS = False
load_dotenv()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_float(x: Any, default: float = 0.0) -> float:
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

        contracts = safe_float(p.get("contracts"), 0.0)
        entry = safe_float(p.get("entryPrice"), 0.0)
        upnl = safe_float(p.get("unrealizedPnl"), 0.0)

        # Keep only meaningful rows (some exchanges return empty placeholders)
        if contracts == 0.0 and upnl == 0.0:
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


def collect() -> None:
    ex = make_exchange()
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
    raw_positions: List[Dict[str, Any]] = []
    try:
        raw_positions = ex.fetch_positions()
    except Exception as e:
        print("fetch_positions failed:", e)

    positions = normalize_ccxt_positions(raw_positions)

    # Insert all normalized positions
    for p in positions:
        con.execute(
            """
            INSERT INTO positions_snapshot
            (created_at, symbol, side, contracts, entry_price, unrealized_pnl)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_ts,
                p["symbol"],
                p["side"],
                p["contracts"],
                p["entryPrice"],
                p["unrealizedPnl"],
            ),
        )

    # ---- Recent fills (MEXC requires symbol; use symbols from positions) ----
    try:
        symbols: Set[str] = {p["symbol"] for p in positions if p.get("symbol")}
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

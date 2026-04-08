from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
import sqlite3
from typing import Any, Optional

import ccxt


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "mexc.sqlite"


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except Exception:
        return None


def _norm_db_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _db_symbol_to_futures_aliases(symbol: str) -> list[str]:
    s = _norm_db_symbol(symbol)
    if not s:
        return []

    out: list[str] = []
    seen = set()

    def _add(x: str) -> None:
        x = str(x or "").strip().upper()
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    _add(s)

    if s.endswith("USDT") and "/" not in s and ":" not in s and len(s) > 4:
        base = s[:-4]
        _add(f"{base}/USDT")
        _add(f"{base}/USDT:USDT")
        _add(f"{base}USDT")

    if "/" in s and ":" not in s:
        _add(s.replace("/", ""))
        _add(f"{s}:USDT")

    if ":" in s:
        left = s.split(":", 1)[0]
        _add(left)
        _add(left.replace("/", ""))

    return out


def _market_aliases(market: dict) -> set[str]:
    out = set()

    symbol = str(market.get("symbol") or "").strip().upper()
    market_id = str(market.get("id") or "").strip().upper()
    base = str(market.get("base") or "").strip().upper()
    quote = str(market.get("quote") or "").strip().upper()
    settle = str(market.get("settle") or "").strip().upper()

    if symbol:
        out.add(symbol)

    if market_id:
        out.add(market_id)

    if base and quote:
        out.add(f"{base}/USDT" if quote == "USDT" else f"{base}/{quote}")
        out.add(f"{base}{quote}")
        if settle:
            out.add(f"{base}/{quote}:{settle}")

    return {x for x in out if x}


def _find_swap_market_for_db_symbol(markets: dict, db_symbol: str) -> Optional[dict]:
    aliases = set(_db_symbol_to_futures_aliases(db_symbol))
    if not aliases:
        return None

    candidates = []
    for market in markets.values():
        if not market.get("swap"):
            continue
        market_syms = _market_aliases(market)
        if aliases & market_syms:
            candidates.append(market)

    if not candidates:
        return None

    # предпочитаме linear USDT swap
    for market in candidates:
        if market.get("linear") and str(market.get("quote") or "").upper() == "USDT":
            return market

    return candidates[0]


def main() -> None:
    print("ROOT_DIR =", ROOT_DIR)
    print("DB_PATH =", DB_PATH)
    print("DB exists =", DB_PATH.exists())

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    ex = ccxt.mexc({
        "options": {
            "defaultType": "swap",
        }
    })

    print("Loading MEXC markets...")
    markets = ex.load_markets()
    print(f"Loaded markets: {len(markets)}")

    rows = con.execute("""
        SELECT *
        FROM symbols_state
        ORDER BY symbol
    """).fetchall()

    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat()

    updated = 0
    missing = []

    for row in rows:
        db_symbol = str(row["symbol"] or "").strip()
        market = _find_swap_market_for_db_symbol(markets, db_symbol)

        if not market:
            print(f"[MISS] {db_symbol}")
            missing.append(db_symbol)
            continue

        precision = market.get("precision") or {}
        limits = market.get("limits") or {}
        info = market.get("info") or {}

        price_precision_value = precision.get("price")
        qty_precision_value = precision.get("amount")

        price_tick = _safe_float(price_precision_value)
        qty_step = _safe_float(qty_precision_value)

        amount_limits = limits.get("amount") or {}
        min_qty = _safe_float(amount_limits.get("min"))

        # fallback-ове при липсващи стойности
        if qty_step is None:
            qty_step = _safe_float(info.get("volUnit"))

        if min_qty is None:
            min_qty = qty_step

        # precision columns като брой десетични, ако може да се изведе
        price_precision = None
        qty_precision = None

        if price_tick is not None and price_tick > 0:
            s = f"{price_tick:.16f}".rstrip("0").rstrip(".")
            if "." in s:
                price_precision = len(s.split(".", 1)[1])
            else:
                price_precision = 0

        if qty_step is not None and qty_step > 0:
            s = f"{qty_step:.16f}".rstrip("0").rstrip(".")
            if "." in s:
                qty_precision = len(s.split(".", 1)[1])
            else:
                qty_precision = 0

        exchange_symbol = (
            market.get("symbol")
            or market.get("id")
            or db_symbol
        )

        con.execute(
            """
            UPDATE symbols_state
            SET
                updated_at = ?,
                exchange_symbol = ?,
                price_tick = ?,
                qty_step = ?,
                min_qty = ?,
                price_precision = ?,
                qty_precision = ?,
                note = COALESCE(note, 'Seed')
            WHERE symbol = ?
            """,
            (
                now_iso,
                str(exchange_symbol),
                price_tick,
                qty_step,
                min_qty,
                price_precision,
                qty_precision,
                db_symbol,
            ),
        )

        updated += 1
        print(
            f"[OK] {db_symbol} -> exchange_symbol={exchange_symbol}, "
            f"price_tick={price_tick}, qty_step={qty_step}, min_qty={min_qty}, "
            f"price_precision={price_precision}, qty_precision={qty_precision}, "
            f"swap={market.get('swap')}, contract={market.get('contract')}, linear={market.get('linear')}"
        )

    con.commit()

    print("\n=== latest symbols_state rows ===")
    out_rows = con.execute("""
        SELECT
            symbol,
            exchange_symbol,
            price_tick,
            qty_step,
            min_qty,
            price_precision,
            qty_precision,
            updated_at
        FROM symbols_state
        ORDER BY symbol
    """).fetchall()

    for r in out_rows:
        print(dict(r))

    print(f"\nUpdated rows: {updated}")
    if missing:
        print("Missing symbols:", missing)

    con.close()


if __name__ == "__main__":
    main()
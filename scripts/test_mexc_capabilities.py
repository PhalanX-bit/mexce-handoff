from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import ccxt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short(obj: Any, limit: int = 2000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        s = repr(obj)
    if len(s) <= limit:
        return s
    return s[:limit] + "\n... [truncated]"


def make_exchange() -> ccxt.Exchange:
    load_dotenv()

    api_key = os.getenv("MEXC_API_KEY")
    api_secret = os.getenv("MEXC_API_SECRET")

    if not api_key or not api_secret:
        raise RuntimeError("Missing MEXC_API_KEY / MEXC_API_SECRET in .env")

    ex = ccxt.mexc(
        {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        }
    )
    return ex


def run_test(name: str, fn):
    print(f"\n=== {name} ===")
    try:
        result = fn()
        print("OK")
        print(short(result))
        return True, result
    except Exception as e:
        print("FAILED")
        print(type(e).__name__, str(e))
        return False, None


def main():
    print("TS:", now_utc_iso())
    ex = make_exchange()

    symbol = "SOL/USDT"
    alt_symbol = "BTC/USDT"

    run_test("load_markets", lambda: ex.load_markets())

    run_test(
        "market info SOL/USDT",
        lambda: ex.market(symbol),
    )

    run_test(
        "fetch_balance",
        lambda: ex.fetch_balance(),
    )

    run_test(
        "fetch_positions",
        lambda: ex.fetch_positions(),
    )

    run_test(
        "fetch_position single symbol via fetch_positions([symbol])",
        lambda: ex.fetch_positions([symbol]),
    )

    run_test(
        "fetch_ticker SOL/USDT",
        lambda: ex.fetch_ticker(symbol),
    )

    run_test(
        "fetch_ticker BTC/USDT",
        lambda: ex.fetch_ticker(alt_symbol),
    )

    run_test(
        "fetch_order_book SOL/USDT",
        lambda: ex.fetch_order_book(symbol, 5),
    )

    run_test(
        "fetch_open_orders SOL/USDT",
        lambda: ex.fetch_open_orders(symbol),
    )

    run_test(
        "fetch_open_orders BTC/USDT",
        lambda: ex.fetch_open_orders(alt_symbol),
    )

    run_test(
        "fetch_closed_orders SOL/USDT",
        lambda: ex.fetch_closed_orders(symbol, limit=10),
    )

    run_test(
        "fetch_closed_orders BTC/USDT",
        lambda: ex.fetch_closed_orders(alt_symbol, limit=10),
    )

    run_test(
        "fetch_orders SOL/USDT",
        lambda: ex.fetch_orders(symbol, limit=10),
    )

    run_test(
        "fetch_my_trades SOL/USDT",
        lambda: ex.fetch_my_trades(symbol, limit=10),
    )

    run_test(
        "fetch_my_trades BTC/USDT",
        lambda: ex.fetch_my_trades(alt_symbol, limit=10),
    )

    run_test(
        "fetch_open_orders SOL/USDT:USDT",
        lambda: ex.fetch_open_orders("SOL/USDT:USDT"),
    )

    run_test(
        "fetch_closed_orders SOL/USDT:USDT",
        lambda: ex.fetch_closed_orders("SOL/USDT:USDT", limit=10),
    )

    run_test(
        "fetch_orders SOL/USDT:USDT",
        lambda: ex.fetch_orders("SOL/USDT:USDT", limit=10),
    )

    run_test(
        "fetch_my_trades SOL/USDT:USDT",
        lambda: ex.fetch_my_trades("SOL/USDT:USDT", limit=10),
    )
    print("\n=== HAS FLAGS ===")
    print(short(ex.has, limit=4000))


if __name__ == "__main__":
    main()
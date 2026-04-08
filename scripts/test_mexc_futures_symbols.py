from __future__ import annotations

import os
import sys
from pathlib import Path
import json

import ccxt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def short(obj, limit=2000):
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        s = repr(obj)
    if len(s) <= limit:
        return s
    return s[:limit] + "\n... [truncated]"


def make_exchange():
    load_dotenv()
    return ccxt.mexc(
        {
            "apiKey": os.getenv("MEXC_API_KEY"),
            "secret": os.getenv("MEXC_API_SECRET"),
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
            },
        }
    )


def try_call(title, fn):
    print(f"\n=== {title} ===")
    try:
        res = fn()
        print("OK")
        print(short(res))
    except Exception as e:
        print("FAILED")
        print(type(e).__name__, str(e))


def main():
    ex = make_exchange()
    ex.load_markets()

    test_symbols = [
        "SOL/USDT",
        "SOL/USDT:USDT",
        "BTC/USDT",
        "BTC/USDT:USDT",
        "ADA/USDT",
        "ADA/USDT:USDT",
    ]

    for sym in test_symbols:
        try_call(f"market {sym}", lambda sym=sym: ex.market(sym))
        try_call(f"fetch_ticker {sym}", lambda sym=sym: ex.fetch_ticker(sym))
        try_call(f"fetch_order_book {sym}", lambda sym=sym: ex.fetch_order_book(sym, 5))


if __name__ == "__main__":
    main()
from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

import ccxt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short(obj, limit: int = 2500) -> str:
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


def try_variant(title: str, fn):
    print(f"\n=== {title} ===")
    try:
        result = fn()
        print("OK")
        print(short(result))
        return True, result
    except Exception as e:
        print("FAILED")
        print(type(e).__name__, str(e))
        return False, None


def try_cleanup_cancel(ex: ccxt.Exchange, created_obj):
    if not created_obj:
        return

    order_id = created_obj.get("id")
    symbol = created_obj.get("symbol")

    if not order_id or not symbol:
        return

    print("\n--- CLEANUP CANCEL ---")
    try:
        res = ex.cancel_order(order_id, symbol)
        print("CANCEL OK")
        print(short(res))
    except Exception as e:
        print("CANCEL FAILED")
        print(type(e).__name__, str(e))


def main():
    print("TS:", now_utc_iso())

    ex = make_exchange()
    ex.load_markets()

    futures_symbol = "BTC/USDT:USDT"
    raw_symbol = "BTC_USDT"
    spot_style_symbol = "BTC/USDT"

    ticker = ex.fetch_ticker(futures_symbol)
    last_price = float(ticker["last"])

    # безопасен далечен sell limit
    price = round(last_price * 1.20, 1)
    amount = 1.0

    print("\n=== BASE TEST DATA ===")
    print(
        short(
            {
                "futures_symbol": futures_symbol,
                "raw_symbol": raw_symbol,
                "spot_style_symbol": spot_style_symbol,
                "last_price": last_price,
                "test_side": "sell",
                "test_type": "limit",
                "test_amount": amount,
                "test_price": price,
            }
        )
    )

    variants = [
        {
            "title": "create_order futures symbol no params",
            "call": lambda: ex.create_order(
                symbol=futures_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
            ),
        },
        {
            "title": "create_order futures symbol with openType=2",
            "call": lambda: ex.create_order(
                symbol=futures_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
                params={"openType": 2},
            ),
        },
        {
            "title": "create_order futures symbol with openType=2 reduceOnly=False",
            "call": lambda: ex.create_order(
                symbol=futures_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
                params={"openType": 2, "reduceOnly": False},
            ),
        },
        {
            "title": "create_order futures symbol with openType=2 positionMode=1",
            "call": lambda: ex.create_order(
                symbol=futures_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
                params={"openType": 2, "positionMode": 1},
            ),
        },
        {
            "title": "create_order futures symbol with openType=2 marginMode=cross",
            "call": lambda: ex.create_order(
                symbol=futures_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
                params={"openType": 2, "marginMode": "cross"},
            ),
        },
        {
            "title": "create_limit_sell_order futures symbol no params",
            "call": lambda: ex.create_limit_sell_order(
                symbol=futures_symbol,
                amount=amount,
                price=price,
            ),
        },
        {
            "title": "create_limit_sell_order futures symbol with openType=2",
            "call": lambda: ex.create_limit_sell_order(
                symbol=futures_symbol,
                amount=amount,
                price=price,
                params={"openType": 2},
            ),
        },
        {
            "title": "create_order raw symbol no params",
            "call": lambda: ex.create_order(
                symbol=raw_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
            ),
        },
        {
            "title": "create_order raw symbol with openType=2",
            "call": lambda: ex.create_order(
                symbol=raw_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
                params={"openType": 2},
            ),
        },
        {
            "title": "create_order spot-style symbol with openType=2",
            "call": lambda: ex.create_order(
                symbol=spot_style_symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=price,
                params={"openType": 2},
            ),
        },
    ]

    first_success_obj = None

    for v in variants:
        ok, res = try_variant(v["title"], v["call"])
        if ok and isinstance(res, dict) and res.get("id"):
            first_success_obj = res
            print("\n*** FIRST SUCCESS DETECTED - STOPPING FURTHER CREATE TESTS ***")
            break

    if first_success_obj:
        print("\n=== FIRST SUCCESS SUMMARY ===")
        print(short(first_success_obj, 3000))

        print("\n=== FETCH OPEN ORDERS BTC/USDT:USDT ===")
        try:
            open_orders = ex.fetch_open_orders(futures_symbol)
            print(short(open_orders, 4000))
        except Exception as e:
            print("FETCH OPEN ORDERS FAILED")
            print(type(e).__name__, str(e))

        try_cleanup_cancel(ex, first_success_obj)
    else:
        print("\n=== RESULT ===")
        print("No create variant succeeded.")


if __name__ == "__main__":
    main()
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


def short(obj, limit: int = 3000) -> str:
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


def main():
    print("TS:", now_utc_iso())

    ex = make_exchange()
    ex.load_markets()

    # Избери futures symbol, който НЕ ползваш активно в стратегията
    symbol = "BTC/USDT:USDT"

    market = ex.market(symbol)
    print("\n=== MARKET ===")
    print(short(market, 2000))

    ticker = ex.fetch_ticker(symbol)
    last_price = float(ticker["last"])
    print("\n=== TICKER ===")
    print(short(ticker, 1500))

    # Безопасен далечен SELL LIMIT над пазара
    # Ако искаш BUY LIMIT вместо това, може да го обърнем.
    price = round(last_price * 1.20, 1)   # 20% над пазара за BTC perpetual
    amount = 1.0                          # 1 контракт

    print("\n=== TEST PLAN ===")
    print(
        short(
            {
                "symbol": symbol,
                "side": "sell",
                "type": "limit",
                "amount": amount,
                "price": price,
                "reduceOnly": False,
                "expected": "create -> visible in open_orders -> cancel",
            }
        )
    )

    created = None
    order_id = None

    try:
        print("\n=== CREATE ORDER ===")
        created = ex.create_order(
            symbol=symbol,
            type="limit",
            side="sell",
            amount=amount,
            price=price,
            params={
                # ако MEXC/CCXT игнорира част от params, не е фатално
                "openType": 2,       # cross
                "positionMode": 1,   # hedge mode / one-way depends on account, just informative
                "reduceOnly": False,
            },
        )
        print("CREATE OK")
        print(short(created, 2500))

        order_id = created.get("id")
        if not order_id:
            raise RuntimeError("Create succeeded but no order id returned.")

        print("\n=== FETCH OPEN ORDERS AFTER CREATE ===")
        open_orders = ex.fetch_open_orders(symbol)
        print(short(open_orders, 4000))

        found = [o for o in open_orders if str(o.get("id")) == str(order_id)]
        print("\n=== ORDER FOUND IN OPEN ORDERS ===")
        print(short(found, 2000))

        print("\n=== CANCEL ORDER ===")
        canceled = ex.cancel_order(order_id, symbol)
        print("CANCEL OK")
        print(short(canceled, 2500))

        print("\n=== FETCH OPEN ORDERS AFTER CANCEL ===")
        open_orders_after = ex.fetch_open_orders(symbol)
        print(short(open_orders_after, 4000))

        found_after = [o for o in open_orders_after if str(o.get("id")) == str(order_id)]
        print("\n=== ORDER STILL PRESENT AFTER CANCEL ===")
        print(short(found_after, 2000))

        print("\n=== RESULT ===")
        print(
            short(
                {
                    "created_order_id": order_id,
                    "found_after_create": len(found) > 0,
                    "still_present_after_cancel": len(found_after) > 0,
                }
            )
        )

    except Exception as e:
        print("\n=== TEST FAILED ===")
        print(type(e).__name__, str(e))

        # ако create е минал, опитай cleanup cancel
        if order_id:
            try:
                print("\n=== CLEANUP CANCEL ATTEMPT ===")
                cleanup = ex.cancel_order(order_id, symbol)
                print(short(cleanup, 2500))
            except Exception as cleanup_err:
                print("CLEANUP FAILED:", type(cleanup_err).__name__, str(cleanup_err))


if __name__ == "__main__":
    main()
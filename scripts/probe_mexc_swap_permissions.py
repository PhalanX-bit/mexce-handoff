import os
import json
from datetime import datetime, timezone

import ccxt
from dotenv import load_dotenv

load_dotenv()

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def j(x):
    return json.dumps(x, ensure_ascii=False, indent=2, default=str)

def make_exchange():
    api_key = os.getenv("MEXC_API_KEY")
    api_secret = os.getenv("MEXC_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("Missing MEXC_API_KEY / MEXC_API_SECRET in .env")

    ex = ccxt.mexc({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    return ex

def try_call(name, fn):
    print("\n" + "="*80)
    print(f"[{now()}] TRY {name}")
    try:
        out = fn()
        print("OK")
        if isinstance(out, (list, tuple)):
            print(f"len={len(out)}")
            print(j(out[:3]))
        else:
            print(j(out))
        return out, None
    except Exception as e:
        print("FAIL:", repr(e))
        return None, e

def main():
    symbol = os.getenv("MEXC_PROBE_SYMBOL", "ADA/USDT")

    ex = make_exchange()

    # load markets helps for swap symbols & endpoints
    try_call("load_markets()", lambda: ex.load_markets())

    # basic sanity
    try_call("fetch_balance()", lambda: ex.fetch_balance())
    try_call(f"fetch_ticker({symbol})", lambda: ex.fetch_ticker(symbol))
    try_call("fetch_positions()", lambda: ex.fetch_positions())

    # the critical ones for fills reconstruction via ORDERS
    open_orders, _ = try_call(f"fetch_open_orders({symbol})", lambda: ex.fetch_open_orders(symbol, limit=20))

    closed_orders, _ = try_call(
        f"fetch_closed_orders({symbol})",
        lambda: ex.fetch_closed_orders(symbol, limit=20)
    )

    all_orders, _ = try_call(
        f"fetch_orders({symbol})",
        lambda: ex.fetch_orders(symbol, limit=20)
    )

    # pick an order id to drill into
    order_id = None
    src = None
    for candidate, label in [(open_orders, "open"), (closed_orders, "closed"), (all_orders, "all")]:
        if isinstance(candidate, list) and candidate:
            oid = candidate[0].get("id") or candidate[0].get("order") or candidate[0].get("orderId")
            if oid:
                order_id = oid
                src = label
                break

    if order_id:
        try_call(f"fetch_order(id={order_id}, symbol={symbol}) [from {src}]", lambda: ex.fetch_order(order_id, symbol))
        try_call(f"fetch_order_trades(id={order_id}, symbol={symbol}) [from {src}]", lambda: ex.fetch_order_trades(order_id, symbol))
    else:
        print("\nNo order_id found from open/closed/all orders to test fetch_order/fetch_order_trades.")

    # funding
    try_call(f"fetch_funding_history({symbol})", lambda: ex.fetch_funding_history(symbol, limit=20))

    # the one that currently fails for you (keep last)
    try_call(f"fetch_my_trades({symbol})", lambda: ex.fetch_my_trades(symbol, limit=20))

if __name__ == "__main__":
    main()

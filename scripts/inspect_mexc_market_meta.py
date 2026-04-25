from __future__ import annotations

import json
import ccxt


SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"]


def main() -> None:
    ex = ccxt.mexc({
        "options": {
            "defaultType": "swap",
        }
    })

    print("Loading MEXC markets...")
    markets = ex.load_markets()
    print(f"Loaded markets: {len(markets)}")

    for sym in SYMBOLS:
        print("\n" + "=" * 100)
        print("SYMBOL:", sym)
        market = markets.get(sym)
        if not market:
            print("NOT FOUND")
            continue

        print("\n--- top-level keys ---")
        print(sorted(market.keys()))

        print("\n--- selected top-level fields ---")
        selected = {
            "symbol": market.get("symbol"),
            "id": market.get("id"),
            "base": market.get("base"),
            "quote": market.get("quote"),
            "settle": market.get("settle"),
            "type": market.get("type"),
            "spot": market.get("spot"),
            "swap": market.get("swap"),
            "future": market.get("future"),
            "contract": market.get("contract"),
            "linear": market.get("linear"),
            "inverse": market.get("inverse"),
            "precision": market.get("precision"),
            "limits": market.get("limits"),
            "tickSize": market.get("tickSize"),
            "priceIncrement": market.get("priceIncrement"),
            "stepSize": market.get("stepSize"),
            "amountIncrement": market.get("amountIncrement"),
        }
        print(json.dumps(selected, ensure_ascii=False, indent=2, default=str))

        info = market.get("info") or {}

        print("\n--- info keys ---")
        print(sorted(info.keys()))

        print("\n--- full info ---")
        print(json.dumps(info, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
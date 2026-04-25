from __future__ import annotations

import json
from typing import Any

from core.mexc_direct import list_open_orders_raw


SYMBOL = "BTC/USDT:USDT"


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def main() -> None:
    items = list_open_orders_raw(SYMBOL)
    print(safe_json({"symbol": SYMBOL, "count": len(items), "items": items}))


if __name__ == "__main__":
    main()
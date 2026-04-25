from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.api_executor import normalize_action


def _base_action() -> dict:
    return {
        "id": 1,
        "symbol": "BTC/USDT:USDT",
        "panel_mode": "OPEN",
        "side": "SHORT",
        "order_kind": "LIMIT",
        "qty": 1.0,
        "limit_price": 70000.0,
        "qty_unit": "contracts",
        "reduce_only": 0,
    }


def main() -> None:
    action = _base_action()
    action["leverage"] = 100
    normalized = normalize_action(action)
    assert normalized["leverage"] == 100

    missing = _base_action()
    try:
        normalize_action(missing)
    except ValueError as exc:
        assert "missing leverage" in str(exc).lower(), str(exc)
    else:
        raise AssertionError("Expected ValueError for missing leverage")

    invalid = _base_action()
    invalid["leverage"] = 0
    try:
        normalize_action(invalid)
    except ValueError as exc:
        assert "invalid leverage" in str(exc).lower(), str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid leverage")

    print("OK: api_executor normalize_action validates leverage early.")


if __name__ == "__main__":
    main()

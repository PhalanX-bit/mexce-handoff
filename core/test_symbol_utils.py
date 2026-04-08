from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.symbol_utils import build_symbol_aliases, canonical_futures_symbol, symbols_match


def main() -> None:
    assert canonical_futures_symbol("ADA/USDT") == "ADA/USDT:USDT"
    assert canonical_futures_symbol("ADA/USDT:USDT") == "ADA/USDT:USDT"
    assert canonical_futures_symbol("ADA_USDT") == "ADA/USDT:USDT"
    assert canonical_futures_symbol("ADAUSDT") == "ADA/USDT:USDT"
    assert canonical_futures_symbol("BTC/USDT/USDT") == "BTC/USDT:USDT"

    aliases = build_symbol_aliases("BTC/USDT:USDT")
    assert aliases == ["BTC/USDT", "BTC/USDT:USDT", "BTC_USDT"], aliases

    assert symbols_match("ADA/USDT", "ADA/USDT:USDT")
    assert symbols_match("ADA_USDT", "ADA/USDT:USDT")
    assert symbols_match("BTC/USDT/USDT", "BTC/USDT:USDT")

    print("OK: symbol normalization and aliases are stable.")


if __name__ == "__main__":
    main()

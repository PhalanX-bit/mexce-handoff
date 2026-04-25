from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.mexc_direct import (
    futures_symbol_raw,
    get_contract_detail_raw,
    get_contract_meta,
    create_limit_order_raw,
)

symbol = "ADA/USDT:USDT"
raw_symbol = futures_symbol_raw(symbol)

print("symbol =", symbol)
print("raw_symbol =", raw_symbol)

print("\n=== get_contract_meta ===")
meta = get_contract_meta(symbol)
print(meta)

print("\n=== get_contract_detail_raw(symbol) ===")
rows = get_contract_detail_raw(symbol)
print("rows_count =", len(rows))
for i, row in enumerate(rows[:10], 1):
    print(
        f"[{i}] symbol={row.get('symbol')} "
        f"priceUnit={row.get('priceUnit')} "
        f"volUnit={row.get('volUnit')} "
        f"minVol={row.get('minVol')} "
        f"priceScale={row.get('priceScale')} "
        f"volScale={row.get('volScale')} "
        f"maxLeverage={row.get('maxLeverage')} "
        f"apiAllowed={row.get('apiAllowed')}"
    )

print("\n=== dry payload probe ===")
try:
    result = create_limit_order_raw(
        symbol=symbol,
        side="OPEN_SHORT",
        price=0.2433,
        vol=1,
        leverage=300,
        external_oid="debug-test-ada-1",
    )
    print("ORDER_OK =", result)
except Exception as e:
    print("ORDER_ERROR =", repr(e))
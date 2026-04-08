from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, InvalidOperation
from typing import Optional

from core.mexc_direct import get_contract_meta


def safe_contract_meta(symbol: str) -> dict | None:
    try:
        return get_contract_meta(symbol)
    except Exception:
        return None


def safe_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def is_valid_step_value(value: float, step: float) -> bool:
    d_value = safe_decimal(value)
    d_step = safe_decimal(step)

    if d_value is None or d_step is None or d_step <= 0:
        return True

    units = d_value / d_step
    return units == units.to_integral_value()


def _quantize_to_step(value, step, rounding_mode) -> Decimal | None:
    d_value = safe_decimal(value)
    d_step = safe_decimal(step)

    if d_value is None:
        return None
    if d_step is None or d_step <= 0:
        return d_value

    units = (d_value / d_step).quantize(Decimal("1"), rounding=rounding_mode)
    return units * d_step


def floor_to_step(value, step) -> float | None:
    result = _quantize_to_step(value, step, ROUND_DOWN)
    return float(result) if result is not None else None


def ceil_to_step(value, step) -> float | None:
    result = _quantize_to_step(value, step, ROUND_UP)
    return float(result) if result is not None else None


def round_to_step(value, step) -> float | None:
    result = _quantize_to_step(value, step, ROUND_HALF_UP)
    return float(result) if result is not None else None


def normalize_qty_to_contract_rules(symbol: str, qty: float) -> float:
    symbol = _normalize_symbol(symbol)
    qty = float(qty)

    if qty <= 0:
        return 0.0

    meta = safe_contract_meta(symbol)
    if not meta:
        return qty

    qty_step = meta.get("qty_step")
    min_qty = meta.get("min_qty")

    normalized_qty = qty

    if isinstance(qty_step, (int, float)) and float(qty_step) > 0:
        snapped = floor_to_step(normalized_qty, float(qty_step))
        normalized_qty = float(snapped or 0.0)

    if isinstance(min_qty, (int, float)) and float(min_qty) > 0:
        if normalized_qty < float(min_qty):
            return 0.0

    return float(normalized_qty)


def normalize_limit_price_to_contract_rules(
    *,
    symbol: str,
    side: str,
    limit_price: float,
) -> float | None:
    symbol = _normalize_symbol(symbol)
    side = str(side or "").strip().upper()

    if limit_price is None or float(limit_price) <= 0:
        return None

    meta = safe_contract_meta(symbol)
    if not meta:
        return float(limit_price)

    price_tick = meta.get("price_tick")
    if not isinstance(price_tick, (int, float)) or float(price_tick) <= 0:
        return float(limit_price)

    tick = float(price_tick)
    raw_price = float(limit_price)

    if side == "LONG":
        snapped = floor_to_step(raw_price, tick)
    elif side == "SHORT":
        snapped = ceil_to_step(raw_price, tick)
    else:
        snapped = round_to_step(raw_price, tick)

    if snapped is None or float(snapped) <= 0:
        return None

    return float(snapped)


def normalize_order_inputs(
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: Optional[float],
    require_limit_price: bool = True,
) -> dict:
    symbol = _normalize_symbol(symbol)
    side = str(side or "").strip().upper()

    normalized_qty = normalize_qty_to_contract_rules(symbol, float(qty))

    normalized_limit_price = None
    if require_limit_price:
        normalized_limit_price = normalize_limit_price_to_contract_rules(
            symbol=symbol,
            side=side,
            limit_price=float(limit_price) if limit_price is not None else 0.0,
        )

    return {
        "symbol": symbol,
        "side": side,
        "qty": normalized_qty,
        "limit_price": normalized_limit_price,
    }


def validate_contract_constraints(
    *,
    symbol: str,
    qty: float,
    limit_price: Optional[float],
    leverage: Optional[float],
    require_limit_price: bool = True,
) -> None:
    symbol = _normalize_symbol(symbol)
    if not symbol:
        raise ValueError("symbol is required")

    qty = float(qty)
    if qty <= 0:
        raise ValueError("qty must be > 0")

    meta = safe_contract_meta(symbol)
    if not meta:
        if require_limit_price and (limit_price is None or float(limit_price) <= 0):
            raise ValueError("limit_price must be > 0")
        if leverage is None or float(leverage) <= 0:
            raise ValueError("leverage must be > 0")
        return

    min_qty = meta.get("min_qty")
    qty_step = meta.get("qty_step")
    price_tick = meta.get("price_tick")
    max_leverage = meta.get("max_leverage")

    if isinstance(min_qty, (int, float)) and float(min_qty) > 0 and qty < float(min_qty):
        raise ValueError(f"qty {qty} is below min_qty for {symbol}: {min_qty}")

    if isinstance(qty_step, (int, float)) and float(qty_step) > 0:
        if not is_valid_step_value(qty, float(qty_step)):
            raise ValueError(f"qty {qty} is not a valid multiple of qty_step {qty_step} for {symbol}")

    if require_limit_price:
        if limit_price is None or float(limit_price) <= 0:
            raise ValueError("limit_price must be > 0")

        if isinstance(price_tick, (int, float)) and float(price_tick) > 0:
            if not is_valid_step_value(float(limit_price), float(price_tick)):
                raise ValueError(
                    f"limit_price {limit_price} is not a valid multiple of price_tick {price_tick} for {symbol}"
                )

    if leverage is None or float(leverage) <= 0:
        raise ValueError("leverage must be > 0")

    leverage_int = int(float(leverage))
    if isinstance(max_leverage, int) and max_leverage > 0 and leverage_int > max_leverage:
        raise ValueError(f"leverage {leverage_int} exceeds max allowed for {symbol}: {max_leverage}")
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, Optional

from core.mexc_direct import get_live_market_snapshot


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _infer_rounding_direction(*, panel_mode: str, side: str) -> str:
    panel_mode_u = str(panel_mode or "").strip().upper()
    side_u = str(side or "").strip().upper()

    if panel_mode_u == "OPEN" and side_u == "LONG":
        return "down"
    if panel_mode_u == "OPEN" and side_u == "SHORT":
        return "up"
    if panel_mode_u == "CLOSE" and side_u == "LONG":
        return "up"
    if panel_mode_u == "CLOSE" and side_u == "SHORT":
        return "down"
    return "nearest"


def _round_to_tick(
    value: float,
    *,
    tick_size: float,
    direction: str,
) -> Optional[float]:
    d_value = _safe_decimal(value)
    d_tick = _safe_decimal(tick_size)

    if d_value is None or d_tick is None or d_tick <= 0:
        return None

    ratio = d_value / d_tick

    if direction == "down":
        units = ratio.to_integral_value(rounding=ROUND_FLOOR)
    elif direction == "up":
        units = ratio.to_integral_value(rounding=ROUND_CEILING)
    else:
        try:
            units = Decimal(round(float(ratio)))
        except Exception:
            return None

    rounded = units * d_tick
    try:
        return float(rounded.normalize())
    except Exception:
        return None


def _safe_live_snapshot(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        return get_live_market_snapshot(symbol)
    except Exception:
        return None


def get_reference_market_price_debug(
    con,
    *,
    symbol: str,
    prefer_mark_price: bool = False,
) -> Dict[str, Any]:
    _ = con  # интерфейсно оставяме con, за да не чупим call sites

    snapshot = _safe_live_snapshot(symbol)
    if not snapshot:
        return {
            "symbol": symbol,
            "found": False,
            "used_price_field": None,
            "reference_price": None,
            "matched_symbol": None,
            "created_at": None,
            "last_price": None,
            "mark_price": None,
            "index_price": None,
            "bid_price": None,
            "ask_price": None,
            "mid_price": None,
            "price_step": None,
            "price_step_source": None,
            "symbols_state_symbol": None,
            "symbols_state_exchange_symbol": None,
        }

    last_price = _safe_float(snapshot.get("last_price"))
    mark_price = _safe_float(snapshot.get("mark_price"))
    index_price = _safe_float(snapshot.get("index_price"))
    bid_price = _safe_float(snapshot.get("bid_price"))
    ask_price = _safe_float(snapshot.get("ask_price"))
    mid_price = _safe_float(snapshot.get("mid_price"))

    used_price_field = None
    reference_price = None

    if prefer_mark_price:
        if mark_price is not None and mark_price > 0:
            used_price_field = "mark_price"
            reference_price = mark_price
        elif index_price is not None and index_price > 0:
            used_price_field = "index_price"
            reference_price = index_price
        elif last_price is not None and last_price > 0:
            used_price_field = "last_price"
            reference_price = last_price
        elif mid_price is not None and mid_price > 0:
            used_price_field = "mid_price"
            reference_price = mid_price
    else:
        if last_price is not None and last_price > 0:
            used_price_field = "last_price"
            reference_price = last_price
        elif mark_price is not None and mark_price > 0:
            used_price_field = "mark_price"
            reference_price = mark_price
        elif index_price is not None and index_price > 0:
            used_price_field = "index_price"
            reference_price = index_price
        elif mid_price is not None and mid_price > 0:
            used_price_field = "mid_price"
            reference_price = mid_price

    price_step = _safe_float(snapshot.get("price_tick"))
    price_step_source = "contract_detail.priceUnit" if price_step is not None and price_step > 0 else None

    return {
        "symbol": symbol,
        "found": True,
        "used_price_field": used_price_field,
        "reference_price": reference_price,
        "matched_symbol": snapshot.get("matched_ticker_symbol") or snapshot.get("raw_symbol"),
        "created_at": snapshot.get("created_at"),
        "last_price": last_price,
        "mark_price": mark_price,
        "index_price": index_price,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "mid_price": mid_price,
        "price_step": price_step,
        "price_step_source": price_step_source,
        "symbols_state_symbol": snapshot.get("raw_symbol"),
        "symbols_state_exchange_symbol": snapshot.get("exchange_symbol"),
    }


def get_reference_market_price(
    con,
    *,
    symbol: str,
    prefer_mark_price: bool = False,
) -> Optional[float]:
    dbg = get_reference_market_price_debug(
        con,
        symbol=symbol,
        prefer_mark_price=prefer_mark_price,
    )
    return dbg.get("reference_price")


def build_market_target_price(
    *,
    panel_mode: str,
    side: str,
    reference_price: float,
    offset_bps: float,
) -> Optional[float]:
    reference_price = _safe_float(reference_price)
    offset_bps = _safe_float(offset_bps)

    if reference_price is None or reference_price <= 0:
        return None
    if offset_bps is None or offset_bps < 0:
        return None

    panel_mode_u = str(panel_mode or "").strip().upper()
    side_u = str(side or "").strip().upper()
    frac = float(offset_bps) / 10000.0

    if panel_mode_u == "OPEN" and side_u == "LONG":
        return float(reference_price) * (1.0 - frac)

    if panel_mode_u == "OPEN" and side_u == "SHORT":
        return float(reference_price) * (1.0 + frac)

    if panel_mode_u == "CLOSE" and side_u == "LONG":
        return float(reference_price) * (1.0 + frac)

    if panel_mode_u == "CLOSE" and side_u == "SHORT":
        return float(reference_price) * (1.0 - frac)

    return None


def build_action_target_price_debug(
    con,
    *,
    action_row: Dict[str, Any],
    open_offset_bps: float,
    close_offset_bps: float,
    prefer_mark_price: bool = False,
    apply_tick_rounding: bool = True,
) -> Dict[str, Any]:
    symbol = str(action_row.get("symbol") or "").strip()
    panel_mode = str(action_row.get("panel_mode") or "").strip().upper()
    side = str(action_row.get("side") or "").strip().upper()

    result = {
        "symbol": symbol,
        "panel_mode": panel_mode,
        "side": side,
        "used_price_field": None,
        "reference_price": None,
        "raw_target_price": None,
        "tick_size": None,
        "tick_size_source": None,
        "rounded_target_price": None,
        "apply_tick_rounding": bool(apply_tick_rounding),
        "matched_symbol": None,
        "ticker_created_at": None,
        "symbols_state_symbol": None,
        "symbols_state_exchange_symbol": None,
    }

    if not symbol or not panel_mode or not side:
        return result

    dbg = get_reference_market_price_debug(
        con,
        symbol=symbol,
        prefer_mark_price=prefer_mark_price,
    )

    result["used_price_field"] = dbg.get("used_price_field")
    result["reference_price"] = dbg.get("reference_price")
    result["matched_symbol"] = dbg.get("matched_symbol")
    result["ticker_created_at"] = dbg.get("created_at")
    result["tick_size"] = dbg.get("price_step")
    result["tick_size_source"] = dbg.get("price_step_source")
    result["symbols_state_symbol"] = dbg.get("symbols_state_symbol")
    result["symbols_state_exchange_symbol"] = dbg.get("symbols_state_exchange_symbol")

    reference_price = dbg.get("reference_price")
    if reference_price is None:
        return result

    if panel_mode == "OPEN":
        offset_bps = float(open_offset_bps)
    elif panel_mode == "CLOSE":
        offset_bps = float(close_offset_bps)
    else:
        return result

    raw_target_price = build_market_target_price(
        panel_mode=panel_mode,
        side=side,
        reference_price=reference_price,
        offset_bps=offset_bps,
    )
    result["raw_target_price"] = raw_target_price

    if raw_target_price is None:
        return result

    if not apply_tick_rounding:
        result["rounded_target_price"] = raw_target_price
        return result

    tick_size = dbg.get("price_step")
    if tick_size is None or tick_size <= 0:
        result["rounded_target_price"] = raw_target_price
        return result

    direction = _infer_rounding_direction(panel_mode=panel_mode, side=side)
    rounded_target = _round_to_tick(
        raw_target_price,
        tick_size=tick_size,
        direction=direction,
    )
    result["rounded_target_price"] = rounded_target if rounded_target is not None else raw_target_price
    return result


def build_action_target_price(
    con,
    *,
    action_row: Dict[str, Any],
    open_offset_bps: float,
    close_offset_bps: float,
    prefer_mark_price: bool = False,
    apply_tick_rounding: bool = True,
) -> Optional[float]:
    dbg = build_action_target_price_debug(
        con,
        action_row=action_row,
        open_offset_bps=open_offset_bps,
        close_offset_bps=close_offset_bps,
        prefer_mark_price=prefer_mark_price,
        apply_tick_rounding=apply_tick_rounding,
    )
    return dbg.get("rounded_target_price")


def build_market_target_price_resolver(
    con,
    *,
    open_offset_bps: float = 5.0,
    close_offset_bps: float = 5.0,
    prefer_mark_price: bool = False,
    apply_tick_rounding: bool = True,
):
    def _resolver(action_row: Dict[str, Any]) -> Optional[float]:
        return build_action_target_price(
            con,
            action_row=action_row,
            open_offset_bps=float(open_offset_bps),
            close_offset_bps=float(close_offset_bps),
            prefer_mark_price=bool(prefer_mark_price),
            apply_tick_rounding=bool(apply_tick_rounding),
        )

    return _resolver
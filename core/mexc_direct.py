from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from core.db import DB_PATH

BASE_URL = "https://api.mexc.com"
DEFAULT_TIMEOUT = 20
DEFAULT_RECV_WINDOW = 10_000

PUBLIC_TICKER_CACHE_TTL_SEC = 1.5
PUBLIC_META_CACHE_TTL_SEC = 1800.0


class MexcDirectError(Exception):
    pass


_PUBLIC_CACHE: Dict[Tuple[str, str], Tuple[float, Any]] = {}
_PUBLIC_CACHE_LOCK = Lock()


def _cache_get(namespace: str, key: str, ttl_sec: float) -> Any:
    now = time.monotonic()
    cache_key = (str(namespace), str(key))

    with _PUBLIC_CACHE_LOCK:
        entry = _PUBLIC_CACHE.get(cache_key)
        if not entry:
            return None

        cached_at, value = entry
        if (now - cached_at) > float(ttl_sec):
            _PUBLIC_CACHE.pop(cache_key, None)
            return None

        return value


def _cache_set(namespace: str, key: str, value: Any) -> Any:
    cache_key = (str(namespace), str(key))
    with _PUBLIC_CACHE_LOCK:
        _PUBLIC_CACHE[cache_key] = (time.monotonic(), value)
    return value


def clear_public_market_cache() -> None:
    with _PUBLIC_CACHE_LOCK:
        _PUBLIC_CACHE.clear()


def _load_keys() -> tuple[str, str]:
    load_dotenv()
    api_key = os.getenv("MEXC_API_KEY", "").strip()
    api_secret = os.getenv("MEXC_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise MexcDirectError("Missing MEXC_API_KEY / MEXC_API_SECRET in .env")

    return api_key, api_secret


def _to_json_compact(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _sign_v1(
    *,
    access_key: str,
    secret_key: str,
    request_time: str,
    param_str: str,
) -> str:
    raw = access_key + request_time + param_str
    return hmac.new(
        secret_key.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _headers(
    *,
    api_key: str,
    api_secret: str,
    param_str: str,
    recv_window: int = DEFAULT_RECV_WINDOW,
) -> Dict[str, str]:
    request_time = str(int(time.time() * 1000))
    signature = _sign_v1(
        access_key=api_key,
        secret_key=api_secret,
        request_time=request_time,
        param_str=param_str,
    )
    return {
        "ApiKey": api_key,
        "Request-Time": request_time,
        "Signature": signature,
        "Recv-Window": str(recv_window),
    }


def _raw_get(
    path: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    api_key, api_secret = _load_keys()
    headers = _headers(api_key=api_key, api_secret=api_secret, param_str="")
    return requests.get(
        BASE_URL + path,
        headers=headers,
        timeout=timeout,
    )


def _raw_public_get(
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    return requests.get(
        BASE_URL + path,
        params=params or {},
        timeout=timeout,
    )


def _raw_post(
    path: str,
    payload: Dict[str, Any] | List[Any],
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> requests.Response:
    api_key, api_secret = _load_keys()
    body_str = _to_json_compact(payload)
    headers = _headers(api_key=api_key, api_secret=api_secret, param_str=body_str)
    headers["Content-Type"] = "application/json"

    return requests.post(
        BASE_URL + path,
        data=body_str.encode("utf-8"),
        headers=headers,
        timeout=timeout,
    )


def _try_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"success": False, "code": resp.status_code, "message": resp.text}


def _ensure_success(data: Any, action: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise MexcDirectError(f"{action} returned non-dict response: {data!r}")

    if data.get("success") is True and int(data.get("code", -1)) == 0:
        return data

    code = data.get("code")
    message = data.get("message")
    raise MexcDirectError(f"{action} failed: code={code} message={message}")


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _ts_ms_to_iso(value: Any) -> Optional[str]:
    ts = _safe_int(value)
    if ts is None or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _quantize_to_step(value: Any, step: Any, rounding) -> Optional[Decimal]:
    d_value = _safe_decimal(value)
    d_step = _safe_decimal(step)

    if d_value is None:
        return None
    if d_step is None or d_step <= 0:
        return d_value

    units = (d_value / d_step).quantize(Decimal("1"), rounding=rounding)
    return units * d_step


def _format_decimal_to_scale(value: Decimal, scale: Optional[int]) -> str:
    if scale is None or scale < 0:
        s = format(value.normalize(), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"

    quant = Decimal("1").scaleb(-int(scale))
    q = value.quantize(quant)
    return format(q, f".{int(scale)}f")


def _decimal_places_from_number(value: Any) -> Optional[int]:
    d = _safe_decimal(value)
    if d is None:
        return None

    normalized = d.normalize()
    exponent = normalized.as_tuple().exponent
    if exponent >= 0:
        return 0
    return abs(int(exponent))


def _infer_price_precision_from_ticker(symbol: str) -> Optional[int]:
    try:
        snap = get_live_ticker_snapshot(symbol)
    except Exception:
        return None

    candidates = [
        snap.get("last_price"),
        snap.get("bid_price"),
        snap.get("ask_price"),
        snap.get("mark_price"),
        snap.get("index_price"),
    ]

    precisions = []
    for value in candidates:
        p = _decimal_places_from_number(value)
        if p is not None:
            precisions.append(p)

    if not precisions:
        return None

    return max(precisions)


def _normalize_price_for_submit(symbol: str, side: str | int, price: float) -> str:
    meta = get_contract_meta(symbol)
    d_price = _safe_decimal(price)
    if d_price is None or d_price <= 0:
        raise ValueError(f"Invalid price for {symbol}: {price}")

    side_str = str(side).strip().upper() if not isinstance(side, int) else str(side)

    if not meta:
        inferred_scale = _infer_price_precision_from_ticker(symbol)
        if inferred_scale is None:
            inferred_scale = 4

        quant = Decimal("1").scaleb(-int(inferred_scale))

        if side_str in {"OPEN_LONG", "CLOSE_SHORT", "1", "2"}:
            snapped = d_price.quantize(quant, rounding=ROUND_DOWN)
        elif side_str in {"OPEN_SHORT", "CLOSE_LONG", "3", "4"}:
            snapped = d_price.quantize(quant, rounding=ROUND_UP)
        else:
            snapped = d_price.quantize(quant, rounding=ROUND_DOWN)

        return format(snapped, f".{int(inferred_scale)}f")

    tick = meta.get("price_tick")
    scale = meta.get("price_precision")

    if side_str in {"OPEN_LONG", "CLOSE_SHORT", "1", "2"}:
        snapped = _quantize_to_step(d_price, tick, ROUND_DOWN)
    elif side_str in {"OPEN_SHORT", "CLOSE_LONG", "3", "4"}:
        snapped = _quantize_to_step(d_price, tick, ROUND_UP)
    else:
        snapped = _quantize_to_step(d_price, tick, ROUND_DOWN)

    if snapped is None or snapped <= 0:
        raise ValueError(f"Unable to normalize price for {symbol}: {price}")

    return _format_decimal_to_scale(snapped, _safe_int(scale))


def _normalize_vol_for_submit(symbol: str, vol: int | float) -> str:
    meta = get_contract_meta(symbol)
    d_vol = _safe_decimal(vol)
    if d_vol is None or d_vol <= 0:
        raise ValueError(f"Invalid vol for {symbol}: {vol}")

    if not meta:
        snapped = d_vol.quantize(Decimal("1"), rounding=ROUND_DOWN)
        if snapped <= 0:
            raise ValueError(f"Invalid fallback vol for {symbol}: {vol}")
        return format(snapped, ".0f")

    qty_step = meta.get("qty_step")
    qty_precision = meta.get("qty_precision")
    min_qty = meta.get("min_qty")

    snapped = _quantize_to_step(d_vol, qty_step, ROUND_DOWN)
    if snapped is None or snapped <= 0:
        raise ValueError(f"Unable to normalize vol for {symbol}: {vol}")

    d_min_qty = _safe_decimal(min_qty)
    if d_min_qty is not None and d_min_qty > 0 and snapped < d_min_qty:
        raise ValueError(f"Normalized vol below min_qty for {symbol}: {snapped} < {d_min_qty}")

    return _format_decimal_to_scale(snapped, _safe_int(qty_precision))


def futures_symbol_raw(symbol: str) -> str:
    s = str(symbol or "").strip().upper()

    if not s:
        raise ValueError("symbol is required")

    if ":" in s:
        s = s.split(":", 1)[0]

    if "/" in s:
        base, quote = s.split("/", 1)
        return f"{base}_{quote}"

    if "_" in s:
        return s

    if s.endswith("USDT"):
        base = s[:-4]
        if base:
            return f"{base}_USDT"

    raise ValueError(f"Unsupported futures symbol format: {symbol}")


def futures_symbol_display(symbol: str) -> str:
    raw = futures_symbol_raw(symbol)
    base, quote = raw.split("_", 1)
    return f"{base}/{quote}:USDT"


def futures_symbol_slash(symbol: str) -> str:
    raw = futures_symbol_raw(symbol)
    base, quote = raw.split("_", 1)
    return f"{base}/{quote}"


def _normalize_side_value(side: str | int) -> int:
    if isinstance(side, int):
        if side in (1, 2, 3, 4):
            return side
        raise ValueError("side int must be one of 1,2,3,4")

    s = str(side).strip().upper()

    mapping = {
        "OPEN_LONG": 1,
        "CLOSE_SHORT": 2,
        "OPEN_SHORT": 3,
        "CLOSE_LONG": 4,
    }

    if s in mapping:
        return mapping[s]

    raise ValueError(
        "side must be one of OPEN_LONG, CLOSE_SHORT, OPEN_SHORT, CLOSE_LONG or 1/2/3/4"
    )


def _extract_order_id(create_response: Dict[str, Any]) -> Optional[int]:
    data = create_response.get("data")

    if isinstance(data, int):
        return data

    if isinstance(data, str) and data.isdigit():
        return int(data)

    if isinstance(data, dict):
        raw = data.get("orderId") or data.get("id")
        if raw is None:
            return None
        try:
            return int(raw)
        except Exception:
            return None

    return None


def _normalize_contract_detail_row(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_symbol = str(row.get("symbol") or "").strip().upper()
    display_symbol = futures_symbol_display(raw_symbol) if raw_symbol else None
    slash_symbol = futures_symbol_slash(raw_symbol) if raw_symbol else None

    return {
        **row,
        "symbol": raw_symbol,
        "exchange_symbol": display_symbol,
        "slash_symbol": slash_symbol,
        "price_tick": _safe_float(row.get("priceUnit")),
        "qty_step": _safe_float(row.get("volUnit")),
        "min_qty": _safe_float(row.get("minVol")),
        "price_precision": _safe_int(row.get("priceScale")),
        "qty_precision": _safe_int(row.get("volScale")),
        "max_leverage": _safe_int(row.get("maxLeverage")),
        "contract_size": _safe_float(row.get("contractSize")),
        "api_allowed": row.get("apiAllowed"),
        "updated_at": _ts_ms_to_iso(row.get("createTime")),
    }


def _extract_contract_detail_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return [x for x in data.get("data", []) if isinstance(x, dict)]
        if isinstance(data.get("data"), dict):
            return [data["data"]]

    return []


def get_contract_detail_raw(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    raw_symbol = futures_symbol_raw(symbol) if symbol else "ALL"
    cached = _cache_get("contract_detail_raw", raw_symbol, PUBLIC_META_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    params: Dict[str, Any] = {}
    if symbol:
        params["symbol"] = futures_symbol_raw(symbol)

    resp = _raw_public_get("/api/v1/contract/detail", params=params)
    data = _try_json(resp)
    ok = _ensure_success(data, "get_contract_detail_raw")
    rows = _extract_contract_detail_items(ok.get("data"))
    return _cache_set("contract_detail_raw", raw_symbol, rows)


def _load_contract_meta_from_symbols_state(symbol: str) -> Optional[Dict[str, Any]]:
    raw_symbol = futures_symbol_raw(symbol)
    display_symbol = futures_symbol_display(raw_symbol)
    slash_symbol = futures_symbol_slash(raw_symbol)
    compact_symbol = slash_symbol.replace("/", "")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT exchange_symbol, symbol, price_tick, qty_step, min_qty, price_precision, qty_precision
            FROM symbols_state
            WHERE UPPER(exchange_symbol) = UPPER(?)
               OR UPPER(symbol) = UPPER(?)
               OR UPPER(symbol) = UPPER(?)
            LIMIT 1
            """,
            (display_symbol, compact_symbol, raw_symbol.replace("_", "")),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return {
        "symbol": raw_symbol,
        "exchange_symbol": str(row["exchange_symbol"] or display_symbol),
        "slash_symbol": slash_symbol,
        "price_tick": _safe_float(row["price_tick"]),
        "qty_step": _safe_float(row["qty_step"]),
        "min_qty": _safe_float(row["min_qty"]),
        "price_precision": _safe_int(row["price_precision"]),
        "qty_precision": _safe_int(row["qty_precision"]),
        "max_leverage": None,
        "contract_meta_source": "symbols_state",
    }


def get_contract_meta(symbol: str) -> Optional[Dict[str, Any]]:
    raw_symbol = futures_symbol_raw(symbol)
    cached = _cache_get("contract_meta", raw_symbol, PUBLIC_META_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    try:
        rows = get_contract_detail_raw(raw_symbol)
    except Exception:
        rows = []

    for row in rows:
        if str(row.get("symbol") or "").upper() == raw_symbol:
            return _cache_set("contract_meta", raw_symbol, _normalize_contract_detail_row(row))

    fallback = _load_contract_meta_from_symbols_state(raw_symbol)
    if fallback is not None:
        return _cache_set("contract_meta", raw_symbol, fallback)

    return None


def get_contract_ticker_raw(symbol: str) -> Dict[str, Any]:
    raw_symbol = futures_symbol_raw(symbol)
    cached = _cache_get("contract_ticker_raw", raw_symbol, PUBLIC_TICKER_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    resp = _raw_public_get("/api/v1/contract/ticker", params={"symbol": raw_symbol})
    data = _try_json(resp)
    ok = _ensure_success(data, "get_contract_ticker_raw")

    payload = ok.get("data")
    if isinstance(payload, dict):
        return _cache_set("contract_ticker_raw", raw_symbol, payload)

    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and str(row.get("symbol") or "").upper() == raw_symbol:
                return _cache_set("contract_ticker_raw", raw_symbol, row)

    raise MexcDirectError(f"get_contract_ticker_raw failed: no ticker row for {raw_symbol}")


def get_live_ticker_snapshot(symbol: str) -> Dict[str, Any]:
    raw_symbol = futures_symbol_raw(symbol)
    cached = _cache_get("live_ticker_snapshot", raw_symbol, PUBLIC_TICKER_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    display_symbol = futures_symbol_display(raw_symbol)
    slash_symbol = futures_symbol_slash(raw_symbol)
    ticker_row = get_contract_ticker_raw(raw_symbol)

    last_price = _safe_float(ticker_row.get("lastPrice"))
    bid1 = _safe_float(ticker_row.get("bid1"))
    ask1 = _safe_float(ticker_row.get("ask1"))
    index_price = _safe_float(ticker_row.get("indexPrice"))
    fair_price = _safe_float(ticker_row.get("fairPrice"))
    funding_rate = _safe_float(ticker_row.get("fundingRate"))
    timestamp_ms = _safe_int(ticker_row.get("timestamp"))

    result = {
        "symbol": display_symbol,
        "exchange_symbol": display_symbol,
        "raw_symbol": raw_symbol,
        "slash_symbol": slash_symbol,
        "matched_ticker_symbol": str(ticker_row.get("symbol") or raw_symbol).upper(),
        "created_at": _ts_ms_to_iso(timestamp_ms),
        "timestamp_ms": timestamp_ms,
        "last_price": last_price,
        "mark_price": fair_price,
        "index_price": index_price,
        "bid_price": bid1,
        "ask_price": ask1,
        "funding_rate": funding_rate,
        "volume24": _safe_float(ticker_row.get("volume24")),
        "amount24": _safe_float(ticker_row.get("amount24")),
        "hold_vol": _safe_float(ticker_row.get("holdVol")),
        "high24_price": _safe_float(ticker_row.get("high24Price")),
        "low24_price": _safe_float(ticker_row.get("lower24Price")),
        "ticker_raw": ticker_row,
    }
    return _cache_set("live_ticker_snapshot", raw_symbol, result)


def get_live_market_snapshot(symbol: str) -> Dict[str, Any]:
    raw_symbol = futures_symbol_raw(symbol)
    cached = _cache_get("live_market_snapshot", raw_symbol, PUBLIC_TICKER_CACHE_TTL_SEC)
    if cached is not None:
        return cached

    ticker = get_live_ticker_snapshot(raw_symbol)
    meta = get_contract_meta(raw_symbol)

    result = {
        **ticker,
        "price_tick": None if not meta else meta.get("price_tick"),
        "qty_step": None if not meta else meta.get("qty_step"),
        "min_qty": None if not meta else meta.get("min_qty"),
        "price_precision": None if not meta else meta.get("price_precision"),
        "qty_precision": None if not meta else meta.get("qty_precision"),
        "max_leverage": None if not meta else meta.get("max_leverage"),
        "contract_meta": meta,
    }
    return _cache_set("live_market_snapshot", raw_symbol, result)


def create_limit_order_raw(
    *,
    symbol: str,
    side: str | int,
    price: float,
    vol: int | float,
    leverage: int,
    open_type: int = 2,
    external_oid: Optional[str] = None,
) -> Dict[str, Any]:
    raw_symbol = futures_symbol_raw(symbol)
    side_value = _normalize_side_value(side)

    normalized_price = _normalize_price_for_submit(raw_symbol, side, price)
    normalized_vol = _normalize_vol_for_submit(raw_symbol, vol)

    payload: Dict[str, Any] = {
        "symbol": raw_symbol,
        "price": normalized_price,
        "vol": normalized_vol,
        "side": side_value,
        "type": int(open_type),
        "openType": int(open_type),
        "leverage": int(leverage),
        "category": 1,
    }

    if external_oid:
        payload["externalOid"] = str(external_oid)

    resp = _raw_post("/api/v1/private/order/submit", payload)
    data = _try_json(resp)
    return _ensure_success(data, "create_limit_order_raw")


def create_market_order_raw(
    *,
    symbol: str,
    side: str | int,
    vol: int | float,
    leverage: int,
    open_type: int = 2,
    external_oid: Optional[str] = None,
) -> Dict[str, Any]:
    raw_symbol = futures_symbol_raw(symbol)
    side_value = _normalize_side_value(side)

    normalized_vol = _normalize_vol_for_submit(raw_symbol, vol)

    payload: Dict[str, Any] = {
        "symbol": raw_symbol,
        "vol": normalized_vol,
        "side": side_value,
        "type": int(open_type),
        "openType": int(open_type),
        "leverage": int(leverage),
        "category": 2,
    }

    if external_oid:
        payload["externalOid"] = str(external_oid)

    resp = _raw_post("/api/v1/private/order/submit", payload)
    data = _try_json(resp)
    return _ensure_success(data, "create_market_order_raw")


def cancel_order_raw(order_id: int) -> Dict[str, Any]:
    payload = [int(order_id)]
    resp = _raw_post("/api/v1/private/order/cancel", payload)
    data = _try_json(resp)
    return _ensure_success(data, "cancel_order_raw")


def list_open_orders_raw(symbol: str) -> List[Dict[str, Any]]:
    raw_symbol = futures_symbol_raw(symbol)
    resp = _raw_get(f"/api/v1/private/order/list/open_orders/{raw_symbol}")
    data = _try_json(resp)
    ok = _ensure_success(data, "list_open_orders_raw")
    items = ok.get("data", [])
    return items if isinstance(items, list) else []


def list_history_orders_raw(symbol: str) -> List[Dict[str, Any]]:
    raw_symbol = futures_symbol_raw(symbol)
    resp = _raw_get(f"/api/v1/private/order/list/history_orders/{raw_symbol}")
    data = _try_json(resp)
    ok = _ensure_success(data, "list_history_orders_raw")
    items = ok.get("data", [])
    return items if isinstance(items, list) else []


def list_orders_raw(symbol: str) -> List[Dict[str, Any]]:
    return list_history_orders_raw(symbol)


def list_deals_raw(symbol: str) -> List[Dict[str, Any]]:
    raw_symbol = futures_symbol_raw(symbol)
    resp = _raw_get(f"/api/v1/private/order/list/order_deals/{raw_symbol}")
    data = _try_json(resp)
    ok = _ensure_success(data, "list_deals_raw")
    items = ok.get("data", [])
    return items if isinstance(items, list) else []


def get_open_order_by_id(symbol: str, order_id: int) -> Optional[Dict[str, Any]]:
    for row in list_open_orders_raw(symbol):
        try:
            if int(row.get("orderId")) == int(order_id):
                return row
        except Exception:
            continue
    return None


def create_limit_order_and_get_id(
    *,
    symbol: str,
    side: str | int,
    price: float,
    vol: int | float,
    leverage: int,
    open_type: int = 2,
    external_oid: Optional[str] = None,
) -> tuple[Dict[str, Any], Optional[int]]:
    data = create_limit_order_raw(
        symbol=symbol,
        side=side,
        price=price,
        vol=vol,
        leverage=leverage,
        open_type=open_type,
        external_oid=external_oid,
    )
    return data, _extract_order_id(data)

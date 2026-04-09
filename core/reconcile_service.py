from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from core.action_queue_order import ACTION_QUEUE_EXECUTOR_ORDER_BY
from core.db import DB_PATH
from core.fill_registry import (
    register_close_fill_from_action,
    register_open_fill_from_action,
)
from core.mexc_direct import (
    futures_symbol_raw,
    get_open_order_by_id,
    list_deals_raw,
    list_history_orders_raw,
    list_open_orders_raw,
    list_orders_raw,
)
from core.streamlit_services.action_ledger_service import (
    build_reconcile_ledger_note,
    log_reconcile_event,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"))


def safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def safe_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@contextmanager
def db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def get_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
    return {r["name"] if isinstance(r, sqlite3.Row) else r[1] for r in rows}


def update_action_row(action_id: int, **fields: Any) -> None:
    with db_conn() as conn:
        cols = get_columns(conn)
        allowed = {k: v for k, v in fields.items() if k in cols}

        if "last_update_at" in cols and "last_update_at" not in allowed:
            allowed["last_update_at"] = utc_now_iso()

        if not allowed:
            return

        parts = []
        values = []
        for k, v in allowed.items():
            parts.append(f"{k} = ?")
            values.append(v)

        values.append(action_id)
        sql = f"UPDATE action_queue SET {', '.join(parts)} WHERE id = ?"
        conn.execute(sql, values)
        conn.commit()


def get_action_by_id(action_id: int) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM action_queue
            WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        return row_to_dict(row)


def _fetch_reconcile_candidates_raw(
    *,
    statuses: Sequence[str],
    max_items: int,
) -> List[Dict[str, Any]]:
    status_placeholders = ",".join("?" for _ in statuses)
    sql = f"""
    SELECT *
    FROM action_queue
    WHERE status IN ({status_placeholders})
      AND api_order_id IS NOT NULL
    ORDER BY {ACTION_QUEUE_EXECUTOR_ORDER_BY}
    LIMIT ?
    """
    params = list(statuses) + [int(max_items)]

    with db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_to_dict(r) for r in rows]


def _match_scope_filters(
    row: Dict[str, Any],
    *,
    symbols: Optional[Sequence[str]],
    created_by_prefixes: Optional[Sequence[str]],
    only_reconcile_states: Optional[Sequence[str]],
    include_null_reconcile_state: bool,
    min_created_at: Optional[str],
    max_age_hours: Optional[float],
) -> bool:
    if symbols:
        if str(row.get("symbol")) not in set(map(str, symbols)):
            return False

    if created_by_prefixes:
        created_by = str(row.get("created_by") or "")
        if not any(created_by.startswith(prefix) for prefix in created_by_prefixes):
            return False

    if only_reconcile_states is not None:
        current_reconcile_state = row.get("reconcile_state")
        allowed = set(map(str, only_reconcile_states))

        if current_reconcile_state in (None, ""):
            if not include_null_reconcile_state:
                return False
        else:
            if str(current_reconcile_state) not in allowed:
                return False

    if min_created_at:
        row_created = parse_iso_utc(row.get("created_at"))
        min_dt = parse_iso_utc(min_created_at)
        if row_created is None or min_dt is None:
            return False
        if row_created < min_dt:
            return False

    if max_age_hours is not None:
        row_created = parse_iso_utc(row.get("created_at"))
        if row_created is None:
            return False
        max_age_delta = timedelta(hours=float(max_age_hours))
        if utc_now() - row_created > max_age_delta:
            return False

    return True


def list_reconcile_candidates(
    *,
    statuses: tuple[str, ...] = ("DONE",),
    max_items: int = 100,
    symbols: Optional[tuple[str, ...]] = None,
    created_by_prefixes: Optional[tuple[str, ...]] = None,
    only_reconcile_states: Optional[tuple[str, ...]] = None,
    include_null_reconcile_state: bool = True,
    min_created_at: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> List[Dict[str, Any]]:
    raw = _fetch_reconcile_candidates_raw(
        statuses=statuses,
        max_items=max_items,
    )

    scoped = [
        row
        for row in raw
        if _match_scope_filters(
            row,
            symbols=symbols,
            created_by_prefixes=created_by_prefixes,
            only_reconcile_states=only_reconcile_states,
            include_null_reconcile_state=include_null_reconcile_state,
            min_created_at=min_created_at,
            max_age_hours=max_age_hours,
        )
    ]
    return scoped


def normalize_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            return [data]

    return []


def extract_order_id(item: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ("orderId", "order_id", "id"):
        if key in item and item[key] not in (None, ""):
            return str(item[key])
    return None


def filter_items_by_order_id(items: List[Dict[str, Any]], api_order_id: str) -> List[Dict[str, Any]]:
    oid = str(api_order_id)
    return [item for item in items if extract_order_id(item) == oid]


def first_item_by_order_id(items: List[Dict[str, Any]], api_order_id: str) -> Optional[Dict[str, Any]]:
    filtered = filter_items_by_order_id(items, api_order_id)
    return filtered[0] if filtered else None


def extract_deal_order_id(item: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ("orderId", "order_id", "id"):
        if key in item and item[key] not in (None, ""):
            return str(item[key])
    return None


def filter_deals_by_order_id(items: List[Dict[str, Any]], api_order_id: str) -> List[Dict[str, Any]]:
    oid = str(api_order_id)
    out = []
    for item in items:
        if isinstance(item, dict) and extract_deal_order_id(item) == oid:
            out.append(item)
    return out


def extract_order_price(item: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for key in ("price", "priceStr"):
        if key in item and item[key] not in (None, ""):
            v = safe_float(item[key])
            if v is not None:
                return v
    return None


def extract_order_vol(item: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for key in ("vol", "volume", "qty"):
        if key in item and item[key] not in (None, ""):
            v = safe_float(item[key])
            if v is not None:
                return v
    return None


def extract_order_deal_vol(item: Optional[Dict[str, Any]]) -> float:
    if not isinstance(item, dict):
        return 0.0
    for key in ("dealVol", "deal_vol", "filledVol", "executedQty"):
        if key in item and item[key] not in (None, ""):
            v = safe_float(item[key])
            if v is not None:
                return v
    return 0.0


def extract_order_state_code(item: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    for key in ("state", "status"):
        if key in item and item[key] not in (None, ""):
            v = safe_int(item[key])
            if v is not None:
                return v
    return None


def extract_avg_price(item: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for key in ("dealAvgPrice", "dealAvgPriceStr", "avgPrice", "avg_price"):
        if key in item and item[key] not in (None, ""):
            v = safe_float(item[key])
            if v is not None:
                return v
    return None


def summarize_deals(deals: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_qty = 0.0
    weighted_notional = 0.0

    for d in deals:
        qty = None
        price = None

        for qk in ("vol", "volume", "qty"):
            if qk in d and d[qk] not in (None, ""):
                qty = safe_float(d[qk])
                if qty is not None:
                    break

        for pk in ("price", "priceStr", "dealPrice", "deal_price"):
            if pk in d and d[pk] not in (None, ""):
                price = safe_float(d[pk])
                if price is not None:
                    break

        if qty is not None:
            total_qty += qty
            if price is not None:
                weighted_notional += qty * price

    avg_price = None
    if total_qty > 0:
        avg_price = weighted_notional / total_qty

    return {
        "deal_count": len(deals),
        "deal_qty": total_qty,
        "deal_avg_price": avg_price,
    }


def safe_source_call(source_name: str, func, *args, **kwargs) -> Dict[str, Any]:
    try:
        payload = func(*args, **kwargs)
        return {
            "ok": True,
            "source": source_name,
            "payload": payload,
            "items": normalize_items(payload),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": source_name,
            "payload": None,
            "items": [],
            "error": f"{exc.__class__.__name__}: {exc}",
        }


@dataclass
class OrderState:
    ok: bool
    symbol: str
    symbol_raw: str
    api_order_id: str
    found_in_open_orders: bool
    found_in_history_orders: bool
    found_in_orders: bool
    found_by_direct_open_lookup: bool
    open_order_item: Dict[str, Any] | None
    history_order_item: Dict[str, Any] | None
    orders_item: Dict[str, Any] | None
    direct_open_item: Dict[str, Any] | None
    deals: List[Dict[str, Any]]
    deal_count: int
    deal_qty: float
    deal_avg_price: float | None
    requested_vol: float | None
    open_deal_vol: float | None
    history_deal_vol: float | None
    resolved_price: float | None
    resolved_avg_price: float | None
    order_state_code: int | None
    lifecycle_state: str
    lifecycle_reason: str
    source_errors: Dict[str, str]
    raw_sources: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "symbol": self.symbol,
            "symbol_raw": self.symbol_raw,
            "api_order_id": self.api_order_id,
            "found_in_open_orders": self.found_in_open_orders,
            "found_in_history_orders": self.found_in_history_orders,
            "found_in_orders": self.found_in_orders,
            "found_by_direct_open_lookup": self.found_by_direct_open_lookup,
            "open_order_item": self.open_order_item,
            "history_order_item": self.history_order_item,
            "orders_item": self.orders_item,
            "direct_open_item": self.direct_open_item,
            "deals": self.deals,
            "deal_count": self.deal_count,
            "deal_qty": self.deal_qty,
            "deal_avg_price": self.deal_avg_price,
            "requested_vol": self.requested_vol,
            "open_deal_vol": self.open_deal_vol,
            "history_deal_vol": self.history_deal_vol,
            "resolved_price": self.resolved_price,
            "resolved_avg_price": self.resolved_avg_price,
            "order_state_code": self.order_state_code,
            "lifecycle_state": self.lifecycle_state,
            "lifecycle_reason": self.lifecycle_reason,
            "source_errors": self.source_errors,
            "raw_sources": self.raw_sources,
        }


def determine_lifecycle(
    *,
    found_in_open_orders: bool,
    found_by_direct_open_lookup: bool,
    found_in_history_orders: bool,
    deal_qty: float,
    requested_vol: float | None,
) -> tuple[str, str]:
    if found_in_open_orders or found_by_direct_open_lookup:
        if deal_qty > 0:
            return "PARTIALLY_FILLED_OPEN", "visible_in_open_scope_with_deals"
        return "OPEN", "visible_in_open_scope"

    if found_in_history_orders:
        if requested_vol is not None and requested_vol > 0:
            if deal_qty >= requested_vol:
                return "FILLED_CONFIRMED", "history_and_deals_confirm_full_fill"
            if deal_qty > 0:
                return "PARTIALLY_FILLED_CLOSED", "history_and_deals_confirm_partial_closed"
            return "CLOSED_CONFIRMED", "history_confirms_closed_no_fill"
        if deal_qty > 0:
            return "FILLED_OR_PARTIAL_CLOSED", "history_present_and_deals_present"
        return "CLOSED_CONFIRMED", "history_confirms_closed"

    if deal_qty > 0:
        return "FILLED_BY_DEALS_ONLY", "deals_present_without_open_or_history"

    return "NOT_FOUND", "not_visible_in_available_sources"


def get_order_state_by_api_order_id(symbol: str, api_order_id: str | int) -> Dict[str, Any]:
    symbol = str(symbol)
    symbol_raw = futures_symbol_raw(symbol)
    api_order_id = str(api_order_id)

    src_open = safe_source_call("open_orders", list_open_orders_raw, symbol)
    src_history = safe_source_call("history_orders", list_history_orders_raw, symbol)
    src_orders = safe_source_call("orders", list_orders_raw, symbol)
    src_deals = safe_source_call("deals", list_deals_raw, symbol)
    src_direct_open = safe_source_call("direct_open_lookup", get_open_order_by_id, symbol, api_order_id)

    open_items = src_open["items"]
    history_items = src_history["items"]
    orders_items = src_orders["items"]
    deals_items = src_deals["items"]

    open_item = first_item_by_order_id(open_items, api_order_id)
    history_item = first_item_by_order_id(history_items, api_order_id)
    orders_item = first_item_by_order_id(orders_items, api_order_id)

    direct_open_item = None
    if src_direct_open["ok"]:
        payload = src_direct_open["payload"]
        if isinstance(payload, dict):
            direct_open_item = payload
        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                direct_open_item = first

    deal_items = filter_deals_by_order_id(deals_items, api_order_id)
    deals_summary = summarize_deals(deal_items)

    requested_vol = (
        extract_order_vol(open_item)
        or extract_order_vol(direct_open_item)
        or extract_order_vol(history_item)
        or extract_order_vol(orders_item)
    )

    open_deal_vol = extract_order_deal_vol(open_item) if open_item is not None else extract_order_deal_vol(direct_open_item)
    history_deal_vol = extract_order_deal_vol(history_item)

    resolved_price = (
        extract_order_price(open_item)
        or extract_order_price(direct_open_item)
        or extract_order_price(history_item)
        or extract_order_price(orders_item)
    )

    resolved_avg_price = (
        extract_avg_price(open_item)
        or extract_avg_price(direct_open_item)
        or extract_avg_price(history_item)
        or extract_avg_price(orders_item)
        or deals_summary["deal_avg_price"]
    )

    order_state_code = (
        extract_order_state_code(open_item)
        or extract_order_state_code(direct_open_item)
        or extract_order_state_code(history_item)
        or extract_order_state_code(orders_item)
    )

    source_errors = {}
    for src in (src_open, src_history, src_orders, src_deals, src_direct_open):
        if not src["ok"] and src["error"]:
            source_errors[src["source"]] = src["error"]

    lifecycle_state, lifecycle_reason = determine_lifecycle(
        found_in_open_orders=open_item is not None,
        found_by_direct_open_lookup=direct_open_item is not None,
        found_in_history_orders=history_item is not None,
        deal_qty=deals_summary["deal_qty"],
        requested_vol=requested_vol,
    )

    state = OrderState(
        ok=True,
        symbol=symbol,
        symbol_raw=symbol_raw,
        api_order_id=api_order_id,
        found_in_open_orders=open_item is not None,
        found_in_history_orders=history_item is not None,
        found_in_orders=orders_item is not None,
        found_by_direct_open_lookup=direct_open_item is not None,
        open_order_item=open_item,
        history_order_item=history_item,
        orders_item=orders_item,
        direct_open_item=direct_open_item,
        deals=deal_items,
        deal_count=deals_summary["deal_count"],
        deal_qty=deals_summary["deal_qty"],
        deal_avg_price=deals_summary["deal_avg_price"],
        requested_vol=requested_vol,
        open_deal_vol=open_deal_vol,
        history_deal_vol=history_deal_vol,
        resolved_price=resolved_price,
        resolved_avg_price=resolved_avg_price,
        order_state_code=order_state_code,
        lifecycle_state=lifecycle_state,
        lifecycle_reason=lifecycle_reason,
        source_errors=source_errors,
        raw_sources={
            "open_orders": src_open["payload"],
            "history_orders": src_history["payload"],
            "orders": src_orders["payload"],
            "deals": src_deals["payload"],
            "direct_open_lookup": src_direct_open["payload"],
        },
    )
    return state.to_dict()


def get_order_state(action_id: int) -> Dict[str, Any]:
    row = get_action_by_id(action_id)
    if not row:
        return {
            "ok": False,
            "stage": "load_action",
            "reason": "action_not_found",
            "action_id": action_id,
        }

    api_order_id = row.get("api_order_id")
    if api_order_id in (None, ""):
        return {
            "ok": False,
            "stage": "precheck",
            "reason": "missing_api_order_id",
            "action_id": action_id,
            "symbol": row.get("symbol"),
        }

    result = get_order_state_by_api_order_id(
        symbol=str(row["symbol"]),
        api_order_id=str(api_order_id),
    )
    result["action_id"] = action_id
    result["action_snapshot"] = {
        "id": row.get("id"),
        "status": row.get("status"),
        "symbol": row.get("symbol"),
        "panel_mode": row.get("panel_mode"),
        "side": row.get("side"),
        "qty": row.get("qty"),
        "limit_price": row.get("limit_price"),
        "api_order_id": row.get("api_order_id"),
        "api_mode": row.get("api_mode"),
    }
    return result


def build_reconcile_update_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "reconcile_state": result.get("lifecycle_state"),
        "reconcile_checked_at": utc_now_iso(),
        "reconcile_reason": result.get("lifecycle_reason"),
        "reconcile_payload": safe_json(
            {
                "api_order_id": result.get("api_order_id"),
                "lifecycle_state": result.get("lifecycle_state"),
                "lifecycle_reason": result.get("lifecycle_reason"),
                "deal_qty": result.get("deal_qty"),
                "requested_vol": result.get("requested_vol"),
                "resolved_price": result.get("resolved_price"),
                "resolved_avg_price": result.get("resolved_avg_price"),
                "found_in_open_orders": result.get("found_in_open_orders"),
                "found_in_history_orders": result.get("found_in_history_orders"),
                "found_by_direct_open_lookup": result.get("found_by_direct_open_lookup"),
                "source_errors": result.get("source_errors"),
            }
        ),
    }


def _resolve_fill_price(result: Dict[str, Any], action_row: Dict[str, Any]) -> Optional[float]:
    for value in (
        result.get("deal_avg_price"),
        result.get("resolved_avg_price"),
        result.get("resolved_price"),
        (action_row or {}).get("limit_price"),
    ):
        v = safe_float(value)
        if v is not None and v > 0:
            return v
    return None


def apply_fill_registry_from_reconcile(action_row: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    lifecycle_state = str(result.get("lifecycle_state") or "").upper()
    deal_qty = safe_float(result.get("deal_qty")) or 0.0
    if deal_qty <= 0:
        return {"applied": False, "reason": "no_deal_qty"}

    if lifecycle_state not in {
        "PARTIALLY_FILLED_OPEN",
        "FILLED_CONFIRMED",
        "PARTIALLY_FILLED_CLOSED",
        "FILLED_OR_PARTIAL_CLOSED",
        "FILLED_BY_DEALS_ONLY",
    }:
        return {"applied": False, "reason": f"lifecycle_not_fill_like:{lifecycle_state}"}

    panel_mode = str(action_row.get("panel_mode") or "OPEN").upper()
    fill_price = _resolve_fill_price(result, action_row)
    if fill_price is None or fill_price <= 0:
        return {"applied": False, "reason": "missing_fill_price"}

    action_id = int(action_row["id"])
    symbol = str(action_row.get("symbol") or "")
    side = str(action_row.get("side") or "").upper()
    action_created_at = action_row.get("created_at")

    with db_conn() as conn:
        if panel_mode == "OPEN":
            fill_result = register_open_fill_from_action(
                conn,
                action_id=action_id,
                symbol=symbol,
                side=side,
                qty_opened=deal_qty,
                entry_price=fill_price,
                target_roi_pct=200.0,
                leverage=safe_float(action_row.get("leverage"), 500.0) or 500.0,
                opened_at=action_created_at,
                source_task_type="ACTION_QUEUE",
                source_task_id=action_id,
            )
        else:
            fill_result = register_close_fill_from_action(
                conn,
                action_id=action_id,
                symbol=symbol,
                side=side,
                close_qty=deal_qty,
                close_price=fill_price,
                closed_at=utc_now_iso(),
                close_task_type="ACTION_QUEUE",
                close_task_id=action_id,
                note=f"reconcile lifecycle={lifecycle_state}",
                eligible_first=True,
            )
        conn.commit()

    return {
        "applied": True,
        "panel_mode": panel_mode,
        "deal_qty": deal_qty,
        "fill_price": fill_price,
        "fill_result": fill_result,
    }


def log_reconcile_ledger_result(action_row: Dict[str, Any], result: Dict[str, Any]) -> bool:
    lifecycle_state = str(result.get("lifecycle_state") or "UNKNOWN").upper()
    action_id = int(action_row["id"])
    note = build_reconcile_ledger_note(
        action_id,
        lifecycle_state,
        deal_qty=safe_float(result.get("deal_qty")),
        resolved_avg_price=safe_float(result.get("resolved_avg_price")),
        extra=f"reason={str(result.get('lifecycle_reason') or '').strip()}",
    )

    with db_conn() as conn:
        inserted = log_reconcile_event(
            conn,
            created_at=utc_now_iso(),
            action_id=action_id,
            symbol=str(action_row.get("symbol") or ""),
            lifecycle_state=lifecycle_state,
            side=str(action_row.get("side") or "").upper() or None,
            qty=safe_float(result.get("deal_qty")) or safe_float(action_row.get("qty")),
            price=_resolve_fill_price(result, action_row),
            note=note,
        )
        conn.commit()
    return bool(inserted)


def reconcile_action(
    action_id: int,
    *,
    update_action_queue: bool = False,
) -> Dict[str, Any]:
    result = get_order_state(action_id)
    if not result.get("ok"):
        return result

    action_row = get_action_by_id(action_id)
    fill_registry_result: Dict[str, Any] = {"applied": False, "reason": "action_not_loaded"}
    if action_row:
        try:
            fill_registry_result = apply_fill_registry_from_reconcile(action_row, result)
        except Exception as exc:
            fill_registry_result = {
                "applied": False,
                "reason": f"{exc.__class__.__name__}: {exc}",
            }
        try:
            result["ledger_logged"] = log_reconcile_ledger_result(action_row, result)
        except Exception as exc:
            result["ledger_logged"] = False
            result["ledger_error"] = f"{exc.__class__.__name__}: {exc}"
    else:
        result["ledger_logged"] = False

    updates: Dict[str, Any] = {}
    if update_action_queue:
        updates = build_reconcile_update_payload(result)
        update_action_row(action_id, **updates)

    result["queue_updated"] = bool(update_action_queue)
    result["queue_update_payload"] = updates
    result["fill_registry"] = fill_registry_result
    return result


def reconcile_open_actions(
    *,
    statuses: tuple[str, ...] = ("DONE",),
    max_items: int = 100,
    update_action_queue: bool = False,
    verbose: bool = True,
    symbols: Optional[tuple[str, ...]] = None,
    created_by_prefixes: Optional[tuple[str, ...]] = None,
    only_reconcile_states: Optional[tuple[str, ...]] = None,
    include_null_reconcile_state: bool = True,
    min_created_at: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> Dict[str, Any]:
    candidates = list_reconcile_candidates(
        statuses=statuses,
        max_items=max_items,
        symbols=symbols,
        created_by_prefixes=created_by_prefixes,
        only_reconcile_states=only_reconcile_states,
        include_null_reconcile_state=include_null_reconcile_state,
        min_created_at=min_created_at,
        max_age_hours=max_age_hours,
    )

    results: List[Dict[str, Any]] = []

    for row in candidates:
        action_id = int(row["id"])
        result = reconcile_action(
            action_id=action_id,
            update_action_queue=update_action_queue,
        )
        results.append(result)

        if verbose:
            print(
                safe_json(
                    {
                        "action_id": action_id,
                        "api_order_id": row.get("api_order_id"),
                        "symbol": row.get("symbol"),
                        "lifecycle_state": result.get("lifecycle_state"),
                        "lifecycle_reason": result.get("lifecycle_reason"),
                        "deal_qty": result.get("deal_qty"),
                        "requested_vol": result.get("requested_vol"),
                        "found_in_open_orders": result.get("found_in_open_orders"),
                        "found_in_history_orders": result.get("found_in_history_orders"),
                        "found_by_direct_open_lookup": result.get("found_by_direct_open_lookup"),
                        "source_errors": result.get("source_errors"),
                    }
                )
            )

    return {
        "ok": True,
        "stage": "reconcile_done",
        "candidates": len(candidates),
        "results": results,
        "service_ts": utc_now_iso(),
    }


if __name__ == "__main__":
    out = reconcile_open_actions(
        statuses=("DONE",),
        max_items=20,
        update_action_queue=False,
        verbose=True,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

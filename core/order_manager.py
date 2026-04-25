from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.api_executor import process_one_action
from core.db import DB_PATH
from core.mexc_direct import (
    cancel_order_raw,
    create_limit_order_and_get_id,
    futures_symbol_raw,
    list_open_orders_raw,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"))


def get_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
    return {r["name"] for r in rows}


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


def get_order_row_by_action_id(action_id: int) -> Optional[Dict[str, Any]]:
    return get_action_by_id(action_id)


def submit_action(action_id: int, verbose: bool = True) -> Optional[Dict[str, Any]]:
    return process_one_action(action_id=action_id, verbose=verbose)


def normalize_open_orders_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]

    return []


def find_open_order(symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
    payload = list_open_orders_raw(symbol)
    items = normalize_open_orders_payload(payload)
    oid = str(order_id)

    for item in items:
        for key in ("orderId", "order_id", "id"):
            if key in item and str(item[key]) == oid:
                return item

    return None


def is_order_open(symbol: str, order_id: str) -> bool:
    return find_open_order(symbol=symbol, order_id=order_id) is not None


def cancel_api_order(order_id: str | int) -> Dict[str, Any]:
    resp = cancel_order_raw(str(order_id))
    return {
        "ok": bool(isinstance(resp, dict) and resp.get("success") is True and resp.get("code") == 0),
        "order_id": str(order_id),
        "response": resp,
    }


def extract_order_id(payload: Any) -> Optional[str]:
    if payload is None:
        return None

    if isinstance(payload, (str, int)):
        return str(payload)

    if isinstance(payload, dict):
        for key in ("order_id", "orderId", "id"):
            if payload.get(key) not in (None, ""):
                return str(payload[key])

        data = payload.get("data")
        if isinstance(data, (str, int)):
            return str(data)
        if isinstance(data, dict):
            for key in ("order_id", "orderId", "id"):
                if data.get(key) not in (None, ""):
                    return str(data[key])

    return None


def normalize_submit_result(result: Any) -> tuple[Optional[str], Any]:
    if isinstance(result, tuple) and len(result) == 2:
        a, b = result

        if isinstance(a, (dict, list)) and isinstance(b, (str, int)):
            return str(b), a

        if isinstance(a, (str, int)) and isinstance(b, (dict, list)):
            return str(a), b

        if isinstance(a, (str, int)) and isinstance(b, (str, int)):
            return str(a), b

        if isinstance(a, (dict, list)):
            return extract_order_id(a) or (str(b) if b is not None else None), a

        return extract_order_id(a) or extract_order_id(b), a

    return extract_order_id(result), result


def replace_limit_order(
    *,
    symbol: str,
    panel_mode: str,
    side: str,
    qty: float,
    new_price: float,
    leverage: int,
    old_order_id: str | int | None = None,
    require_old_open: bool = False,
    cancel_first: bool = True,
) -> Dict[str, Any]:
    symbol = str(symbol).strip()
    panel_mode = str(panel_mode).upper().strip()
    side = str(side).upper().strip()
    qty = float(qty)
    new_price = float(new_price)
    leverage = int(leverage)

    if panel_mode not in {"OPEN", "CLOSE"}:
        raise ValueError(f"Unsupported panel_mode: {panel_mode}")

    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported side: {side}")

    if panel_mode == "OPEN" and side == "LONG":
        api_side = "OPEN_LONG"
    elif panel_mode == "OPEN" and side == "SHORT":
        api_side = "OPEN_SHORT"
    elif panel_mode == "CLOSE" and side == "LONG":
        api_side = "CLOSE_LONG"
    elif panel_mode == "CLOSE" and side == "SHORT":
        api_side = "CLOSE_SHORT"
    else:
        raise ValueError(f"Unsupported mapping: {panel_mode}/{side}")

    old_order_id_str = str(old_order_id) if old_order_id is not None else None

    old_open_before = None
    cancel_info = None

    if old_order_id_str is not None:
        old_open_before = is_order_open(symbol=symbol, order_id=old_order_id_str)

        if require_old_open and not old_open_before:
            return {
                "ok": False,
                "stage": "precheck",
                "reason": "old_order_not_open",
                "symbol": symbol,
                "old_order_id": old_order_id_str,
                "old_open_before": old_open_before,
            }

        if cancel_first and old_open_before:
            cancel_info = cancel_api_order(old_order_id_str)
            if not cancel_info["ok"]:
                return {
                    "ok": False,
                    "stage": "cancel",
                    "reason": "cancel_failed",
                    "symbol": symbol,
                    "old_order_id": old_order_id_str,
                    "old_open_before": old_open_before,
                    "cancel": cancel_info,
                }

    client_oid = f"replace-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

    result = create_limit_order_and_get_id(
        symbol=symbol,
        side=api_side,
        vol=qty,
        price=new_price,
        leverage=leverage,
        external_oid=client_oid,
    )

    new_order_id, raw_submit = normalize_submit_result(result)

    if not new_order_id:
        return {
            "ok": False,
            "stage": "submit",
            "reason": "missing_new_order_id",
            "symbol": symbol,
            "old_order_id": old_order_id_str,
            "cancel": cancel_info,
            "submit_response": raw_submit,
        }

    new_open = is_order_open(symbol=symbol, order_id=new_order_id)

    return {
        "ok": True,
        "stage": "done",
        "symbol": symbol,
        "symbol_raw": futures_symbol_raw(symbol),
        "old_order_id": old_order_id_str,
        "old_open_before": old_open_before,
        "cancel": cancel_info,
        "new_order_id": new_order_id,
        "new_price": new_price,
        "new_qty": qty,
        "new_open_after_submit": new_open,
        "client_oid": client_oid,
        "submit_response": raw_submit,
    }


def replace_limit_order_from_action(
    *,
    action_id: int,
    new_price: float,
    old_order_id: str | int | None = None,
    require_old_open: bool = False,
    cancel_first: bool = True,
    update_action_queue: bool = True,
) -> Dict[str, Any]:
    row = get_action_by_id(action_id)
    if not row:
        return {
            "ok": False,
            "stage": "load_action",
            "reason": "action_not_found",
            "action_id": action_id,
        }

    row_old_order_id = row.get("api_order_id")
    if old_order_id is None:
        old_order_id = row_old_order_id

    result = replace_limit_order(
        symbol=row["symbol"],
        panel_mode=row["panel_mode"],
        side=row["side"],
        qty=float(row["qty"]),
        new_price=float(new_price),
        leverage=int(row["leverage"]) if row.get("leverage") is not None else 1,
        old_order_id=old_order_id,
        require_old_open=require_old_open,
        cancel_first=cancel_first,
    )

    if result.get("ok") and update_action_queue:
        update_action_row(
            action_id,
            api_order_id=result["new_order_id"],
            api_client_oid=result["client_oid"],
            api_submit_path="/api/v1/private/order/submit",
            api_mode="direct_futures_api_replace",
            api_response=safe_json(result.get("submit_response")),
            limit_price=float(new_price),
            last_error=None,
        )

    return result


def extract_open_order_price(order_item: Dict[str, Any]) -> Optional[float]:
    if not isinstance(order_item, dict):
        return None

    for key in ("price", "priceStr"):
        if key in order_item and order_item[key] not in (None, ""):
            try:
                return float(order_item[key])
            except Exception:
                pass

    return None


def compute_price_drift_bps(current_order_price: float, target_price: float) -> float:
    current_order_price = float(current_order_price)
    target_price = float(target_price)

    if target_price == 0:
        raise ValueError("target_price must not be 0")

    return abs(current_order_price - target_price) / abs(target_price) * 10000.0


def needs_reprice(current_order_price: float, target_price: float, threshold_bps: float) -> bool:
    drift_bps = compute_price_drift_bps(current_order_price, target_price)
    return drift_bps >= float(threshold_bps)


def reprice_limit_order_if_needed(
    *,
    action_id: int,
    target_price: float,
    threshold_bps: float = 2.0,
    require_order_open: bool = True,
    update_action_queue: bool = True,
) -> Dict[str, Any]:
    row = get_action_by_id(action_id)
    if not row:
        return {
            "ok": False,
            "stage": "load_action",
            "reason": "action_not_found",
            "action_id": action_id,
        }

    old_order_id = row.get("api_order_id")
    if old_order_id in (None, ""):
        return {
            "ok": False,
            "stage": "precheck",
            "reason": "missing_api_order_id",
            "action_id": action_id,
        }

    symbol = str(row["symbol"])
    old_order_id = str(old_order_id)
    target_price = float(target_price)
    threshold_bps = float(threshold_bps)

    open_item = find_open_order(symbol=symbol, order_id=old_order_id)

    if open_item is None:
        return {
            "ok": False if require_order_open else True,
            "stage": "precheck",
            "reason": "order_not_open",
            "action_id": action_id,
            "old_order_id": old_order_id,
            "target_price": target_price,
        }

    current_order_price = extract_open_order_price(open_item)
    if current_order_price is None:
        return {
            "ok": False,
            "stage": "inspect_open_order",
            "reason": "missing_order_price",
            "action_id": action_id,
            "old_order_id": old_order_id,
            "open_item": open_item,
        }

    drift_bps = compute_price_drift_bps(current_order_price, target_price)

    if not needs_reprice(current_order_price, target_price, threshold_bps):
        return {
            "ok": True,
            "stage": "noop",
            "reason": "within_threshold",
            "action_id": action_id,
            "old_order_id": old_order_id,
            "current_order_price": current_order_price,
            "target_price": target_price,
            "drift_bps": drift_bps,
            "threshold_bps": threshold_bps,
        }

    replace_result = replace_limit_order_from_action(
        action_id=action_id,
        new_price=target_price,
        old_order_id=old_order_id,
        require_old_open=True,
        cancel_first=True,
        update_action_queue=update_action_queue,
    )

    replace_result["stage"] = "replaced" if replace_result.get("ok") else replace_result.get("stage")
    replace_result["action_id"] = action_id
    replace_result["previous_order_id"] = old_order_id
    replace_result["current_order_price"] = current_order_price
    replace_result["target_price"] = target_price
    replace_result["drift_bps"] = drift_bps
    replace_result["threshold_bps"] = threshold_bps
    return replace_result
from __future__ import annotations

import ast
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.db import DB_PATH
from core.mexc_direct import (
    cancel_order_raw,
    create_limit_order_and_get_id,
    futures_symbol_raw,
    get_open_order_by_id,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"), default=str)


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


def normalize_api_order_id(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return s

        # Handle stringified dict/list payloads such as:
        # "{'success': True, 'code': 0, 'data': 795414598814619648}"
        # or '["795414598814619648"]'
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                parsed = ast.literal_eval(s)
                nested = normalize_api_order_id(parsed)
                if nested:
                    return nested
            except Exception:
                pass

        return None

    if isinstance(value, dict):
        for key in ("data", "orderId", "order_id", "id"):
            if key in value:
                nested = normalize_api_order_id(value[key])
                if nested:
                    return nested
        return None

    if isinstance(value, (list, tuple)):
        for item in value:
            nested = normalize_api_order_id(item)
            if nested:
                return nested
        return None

    return None


def normalize_api_response(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return safe_json(value)


@contextmanager
def db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
    return {r["name"] if isinstance(r, sqlite3.Row) else r[1] for r in rows}


def row_to_dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


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


def update_action_row(action_id: int, **fields: Any) -> None:
    with db_conn() as conn:
        cols = get_columns(conn)
        allowed = {k: v for k, v in fields.items() if k in cols}

        if "api_order_id" in allowed:
            allowed["api_order_id"] = normalize_api_order_id(allowed["api_order_id"])

        if "api_response" in allowed:
            allowed["api_response"] = normalize_api_response(allowed["api_response"])

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


@dataclass
class RepriceServiceConfig:
    threshold_bps: float = 2.0
    favorable_threshold_bps: Optional[float] = None
    adverse_threshold_bps: Optional[float] = None
    max_order_age_sec: Optional[float] = None
    require_order_open: bool = True
    update_action_queue: bool = True
    max_reprice_distance_bps: Optional[float] = None


def side_token_from_action(panel_mode: str, side: str) -> str:
    panel_mode = str(panel_mode).strip().upper()
    side = str(side).strip().upper()

    mapping = {
        ("OPEN", "LONG"): "OPEN_LONG",
        ("OPEN", "SHORT"): "OPEN_SHORT",
        ("CLOSE", "LONG"): "CLOSE_LONG",
        ("CLOSE", "SHORT"): "CLOSE_SHORT",
    }

    key = (panel_mode, side)
    if key not in mapping:
        raise ValueError(f"unsupported panel_mode/side combination: {key}")
    return mapping[key]


def classify_overshoot_kind(panel_mode: str, side: str, current_price: float, target_price: float) -> str:
    panel_mode = str(panel_mode).strip().upper()
    side = str(side).strip().upper()

    if abs(target_price - current_price) < 1e-12:
        return "none"

    if panel_mode == "OPEN" and side == "SHORT":
        return "favorable_down" if target_price < current_price else "adverse_up"

    if panel_mode == "OPEN" and side == "LONG":
        return "favorable_up" if target_price > current_price else "adverse_down"

    if panel_mode == "CLOSE" and side == "LONG":
        return "favorable_up" if target_price > current_price else "adverse_down"

    if panel_mode == "CLOSE" and side == "SHORT":
        return "favorable_down" if target_price < current_price else "adverse_up"

    return "unknown"


def resolve_effective_threshold_bps(overshoot_kind: str, config: RepriceServiceConfig) -> float:
    base = float(config.threshold_bps)

    if overshoot_kind.startswith("favorable") and config.favorable_threshold_bps is not None:
        return float(config.favorable_threshold_bps)

    if overshoot_kind.startswith("adverse") and config.adverse_threshold_bps is not None:
        return float(config.adverse_threshold_bps)

    return base


def calc_drift_bps(current_price: float, target_price: float) -> float:
    if current_price == 0:
        return 0.0
    return abs(target_price - current_price) / abs(current_price) * 10000.0


def get_open_order_item(symbol: str, api_order_id: str | int) -> Optional[Dict[str, Any]]:
    normalized_order_id = normalize_api_order_id(api_order_id)
    if not normalized_order_id:
        return None

    try:
        item = get_open_order_by_id(symbol, normalized_order_id)
        if isinstance(item, dict):
            return item
        if isinstance(item, list) and item:
            first = item[0]
            if isinstance(first, dict):
                return first
    except Exception:
        return None
    return None


def compute_order_age_sec(open_order_item: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(open_order_item, dict):
        return None

    create_time_ms = safe_int(open_order_item.get("createTime"))
    if create_time_ms is None:
        return None

    try:
        created_dt = datetime.fromtimestamp(create_time_ms / 1000.0, tz=timezone.utc)
        return max(0.0, (utc_now() - created_dt).total_seconds())
    except Exception:
        return None


def normalize_submit_result(submit: Any) -> tuple[str, Any, str]:
    submit_path = "/api/v1/private/order/submit"
    raw_submit = submit

    if isinstance(submit, tuple):
        parts = list(submit)

        if len(parts) >= 3 and isinstance(parts[2], str):
            submit_path = parts[2]

        for candidate in parts[:2]:
            normalized = normalize_api_order_id(candidate)
            if normalized:
                if isinstance(candidate, dict):
                    raw_submit = candidate
                elif len(parts) >= 2 and isinstance(parts[1], dict):
                    raw_submit = parts[1]
                else:
                    raw_submit = candidate
                return normalized, raw_submit, submit_path

        if len(parts) >= 2:
            if isinstance(parts[0], dict):
                raw_submit = parts[0]
            elif isinstance(parts[1], dict):
                raw_submit = parts[1]
            else:
                raw_submit = parts[0]

        normalized = normalize_api_order_id(raw_submit)
        if normalized:
            return normalized, raw_submit, submit_path

        raise ValueError(f"Could not extract order id from submit result: {submit!r}")

    normalized = normalize_api_order_id(submit)
    if not normalized:
        raise ValueError(f"Could not extract order id from submit result: {submit!r}")

    return normalized, raw_submit, submit_path


def evaluate_reprice_need(
    *,
    action_id: int,
    target_price: float,
    config: RepriceServiceConfig,
) -> Dict[str, Any]:
    row = get_action_by_id(action_id)
    if not row:
        return {
            "ok": False,
            "action_id": action_id,
            "stage": "load_action",
            "reason": "action_not_found",
        }

    api_order_id = normalize_api_order_id(row.get("api_order_id"))
    if not api_order_id:
        return {
            "ok": False,
            "action_id": action_id,
            "stage": "precheck",
            "reason": "missing_api_order_id",
            "symbol": row.get("symbol"),
        }

    symbol = str(row["symbol"])
    panel_mode = str(row["panel_mode"])
    side = str(row["side"])

    open_order_item = get_open_order_item(symbol, api_order_id)
    order_open = open_order_item is not None

    current_order_price = None
    if open_order_item is not None:
        current_order_price = safe_float(open_order_item.get("price"))
        if current_order_price is None:
            current_order_price = safe_float(open_order_item.get("priceStr"))

    if current_order_price is None:
        current_order_price = safe_float(row.get("limit_price"))

    if current_order_price is None:
        return {
            "ok": False,
            "action_id": action_id,
            "stage": "precheck",
            "reason": "missing_current_order_price",
            "symbol": symbol,
            "api_order_id": api_order_id,
        }

    target_price = float(target_price)
    current_order_price = float(current_order_price)

    overshoot_kind = classify_overshoot_kind(
        panel_mode=panel_mode,
        side=side,
        current_price=current_order_price,
        target_price=target_price,
    )

    drift_bps = calc_drift_bps(current_order_price, target_price)
    effective_threshold_bps = resolve_effective_threshold_bps(overshoot_kind, config)
    stale_by_drift = drift_bps >= float(effective_threshold_bps)

    order_age_sec = compute_order_age_sec(open_order_item)
    stale_by_age = False
    if config.max_order_age_sec is not None and order_age_sec is not None:
        stale_by_age = order_age_sec >= float(config.max_order_age_sec)

    too_far_by_distance = False
    if (
        config.max_reprice_distance_bps is not None
        and drift_bps is not None
        and float(drift_bps) > float(config.max_reprice_distance_bps)
    ):
        too_far_by_distance = True

    if abs(target_price - current_order_price) < 1e-12:
        should_replace = False
        decision_reason = "same_price"
    elif config.require_order_open and not order_open:
        should_replace = False
        decision_reason = "order_not_open"
    elif too_far_by_distance:
        should_replace = False
        decision_reason = "too_far_by_distance"
    elif stale_by_drift:
        should_replace = True
        decision_reason = "stale_by_drift"
    elif stale_by_age:
        should_replace = True
        decision_reason = "stale_by_age"
    else:
        should_replace = False
        decision_reason = "within_threshold_and_age"

    return {
        "ok": True,
        "action_id": action_id,
        "symbol": symbol,
        "api_order_id": api_order_id,
        "order_open": order_open,
        "current_order_price": current_order_price,
        "target_price": target_price,
        "drift_bps": drift_bps,
        "threshold_bps": float(config.threshold_bps),
        "effective_threshold_bps": float(effective_threshold_bps),
        "favorable_threshold_bps": config.favorable_threshold_bps,
        "adverse_threshold_bps": config.adverse_threshold_bps,
        "max_reprice_distance_bps": config.max_reprice_distance_bps,
        "too_far_by_distance": too_far_by_distance,
        "order_age_sec": order_age_sec,
        "max_order_age_sec": config.max_order_age_sec,
        "stale_by_drift": stale_by_drift,
        "stale_by_age": stale_by_age,
        "overshoot_kind": overshoot_kind,
        "should_replace": should_replace,
        "decision_reason": decision_reason,
        "open_order_item": open_order_item,
    }


def replace_limit_order_from_action(
    *,
    action_id: int,
    new_price: float,
    new_qty: Optional[float] = None,
    client_oid: Optional[str] = None,
) -> Dict[str, Any]:
    row = get_action_by_id(action_id)
    if not row:
        return {
            "ok": False,
            "stage": "load_action",
            "reason": "action_not_found",
            "action_id": action_id,
        }

    symbol = str(row["symbol"])
    symbol_raw = futures_symbol_raw(symbol)

    api_order_id = normalize_api_order_id(row.get("api_order_id"))
    if not api_order_id:
        return {
            "ok": False,
            "stage": "precheck",
            "reason": "missing_api_order_id",
            "action_id": action_id,
            "symbol": symbol,
        }

    old_order_id = api_order_id
    panel_mode = str(row["panel_mode"])
    side = str(row["side"])
    side_token = side_token_from_action(panel_mode, side)

    qty = float(new_qty if new_qty is not None else row["qty"])
    leverage = safe_int(row.get("leverage"))
    if leverage is None:
        leverage = 500

    old_open_item = get_open_order_item(symbol, old_order_id)
    old_open_before = old_open_item is not None

    cancel_resp = cancel_order_raw(old_order_id)
    cancel_ok = bool(cancel_resp.get("success")) and safe_int(cancel_resp.get("code")) == 0

    cancel_info = {
        "ok": cancel_ok,
        "order_id": old_order_id,
        "response": cancel_resp,
    }

    if not cancel_ok:
        return {
            "ok": False,
            "stage": "cancel_failed",
            "action_id": action_id,
            "symbol": symbol,
            "symbol_raw": symbol_raw,
            "old_order_id": old_order_id,
            "old_open_before": old_open_before,
            "cancel": cancel_info,
        }

    if not client_oid:
        client_oid = f"replace-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{uuid.uuid4().hex[:6]}"

    submit = create_limit_order_and_get_id(
        symbol=symbol,
        side=side_token,
        vol=qty,
        price=float(new_price),
        leverage=int(leverage),
        external_oid=client_oid,
    )

    new_order_id, raw_submit, submit_path = normalize_submit_result(submit)

    new_open_item = get_open_order_item(symbol, new_order_id)
    new_open_after_submit = new_open_item is not None

    update_action_row(
        action_id,
        api_order_id=new_order_id,
        api_client_oid=client_oid,
        api_mode="direct_futures_api_replace",
        api_submit_path=submit_path,
        api_response=raw_submit,
        limit_price=float(new_price),
        last_error=None,
    )

    return {
        "ok": True,
        "stage": "done",
        "action_id": action_id,
        "symbol": symbol,
        "symbol_raw": symbol_raw,
        "old_order_id": old_order_id,
        "old_open_before": old_open_before,
        "cancel": cancel_info,
        "new_order_id": new_order_id,
        "new_price": float(new_price),
        "new_qty": float(qty),
        "new_open_after_submit": new_open_after_submit,
        "client_oid": client_oid,
        "submit_response": raw_submit,
    }


def reprice_action_once(
    *,
    action_id: int,
    target_price: float,
    config: RepriceServiceConfig,
    apply_changes: bool = True,
) -> Dict[str, Any]:
    decision = evaluate_reprice_need(
        action_id=action_id,
        target_price=float(target_price),
        config=config,
    )

    row_now = get_action_by_id(action_id)

    if not decision.get("ok"):
        return decision

    if not decision.get("should_replace"):
        out = dict(decision)
        out["stage"] = "noop"
        out["applied"] = False
        out["service_ts"] = utc_now_iso()
        out["service_mode"] = "apply" if apply_changes else "evaluate_only"
        out["action_snapshot"] = {
            "id": action_id,
            "status": row_now.get("status") if row_now else None,
            "symbol": row_now.get("symbol") if row_now else None,
            "panel_mode": row_now.get("panel_mode") if row_now else None,
            "side": row_now.get("side") if row_now else None,
            "qty": row_now.get("qty") if row_now else None,
            "limit_price": row_now.get("limit_price") if row_now else None,
            "api_order_id": normalize_api_order_id(row_now.get("api_order_id")) if row_now else None,
            "api_mode": row_now.get("api_mode") if row_now else None,
        }
        return out

    if not apply_changes:
        out = dict(decision)
        out["stage"] = "would_replace"
        out["applied"] = False
        out["service_ts"] = utc_now_iso()
        out["service_mode"] = "evaluate_only"
        out["action_snapshot"] = {
            "id": action_id,
            "status": row_now.get("status") if row_now else None,
            "symbol": row_now.get("symbol") if row_now else None,
            "panel_mode": row_now.get("panel_mode") if row_now else None,
            "side": row_now.get("side") if row_now else None,
            "qty": row_now.get("qty") if row_now else None,
            "limit_price": row_now.get("limit_price") if row_now else None,
            "api_order_id": normalize_api_order_id(row_now.get("api_order_id")) if row_now else None,
            "api_mode": row_now.get("api_mode") if row_now else None,
        }
        return out

    row_before = row_now
    previous_order_id = (
        normalize_api_order_id(row_before.get("api_order_id"))
        if row_before
        else None
    )

    replace_result = replace_limit_order_from_action(
        action_id=action_id,
        new_price=float(target_price),
        new_qty=safe_float(row_before.get("qty")) if row_before else None,
    )

    out = dict(replace_result)
    out["stage"] = "replaced" if replace_result.get("ok") else replace_result.get("stage", "replace_failed")
    out["applied"] = bool(replace_result.get("ok"))
    out["service_ts"] = utc_now_iso()
    out["service_mode"] = "apply"
    out["previous_order_id"] = previous_order_id
    out["current_order_price"] = decision.get("current_order_price")
    out["target_price"] = decision.get("target_price")
    out["drift_bps"] = decision.get("drift_bps")
    out["threshold_bps"] = decision.get("effective_threshold_bps")
    out["max_reprice_distance_bps"] = decision.get("max_reprice_distance_bps")
    out["too_far_by_distance"] = decision.get("too_far_by_distance")
    out["policy_decision"] = decision
    out["action_snapshot"] = {
        "id": row_before.get("id") if row_before else action_id,
        "status": row_before.get("status") if row_before else None,
        "symbol": row_before.get("symbol") if row_before else None,
        "panel_mode": row_before.get("panel_mode") if row_before else None,
        "side": row_before.get("side") if row_before else None,
        "qty": row_before.get("qty") if row_before else None,
        "limit_price": row_before.get("limit_price") if row_before else None,
        "api_order_id": normalize_api_order_id(row_before.get("api_order_id")) if row_before else None,
        "api_mode": row_before.get("api_mode") if row_before else None,
    }
    return out
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_dumps_compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def build_reprice_payload(
    *,
    action_id: int,
    old_api_order_id: str | None,
    current_price: float | None,
    target_price: float | None,
    drift_bps: float | None,
    overshoot_kind: str | None,
    effective_threshold_bps: float | None,
    order_age_sec: float | None,
    max_order_age_sec: float | None,
    stale_by_drift: bool | None,
    stale_by_age: bool | None,
    should_replace: bool | None,
    decision_reason: str | None,
    apply_changes: bool,
    new_api_order_id: str | None = None,
    new_api_client_oid: str | None = None,
    replace_result: str | None = None,
    cancel_response: Any | None = None,
    submit_response: Any | None = None,
    submit_error: Any | None = None,
    intended_limit_price: float | None = None,
    intended_client_oid: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_id": action_id,
        "old_api_order_id": old_api_order_id,
        "current_price": current_price,
        "target_price": target_price,
        "drift_bps": drift_bps,
        "overshoot_kind": overshoot_kind,
        "effective_threshold_bps": effective_threshold_bps,
        "order_age_sec": order_age_sec,
        "max_order_age_sec": max_order_age_sec,
        "stale_by_drift": stale_by_drift,
        "stale_by_age": stale_by_age,
        "should_replace": should_replace,
        "decision_reason": decision_reason,
        "apply_changes": apply_changes,
        "new_api_order_id": new_api_order_id,
        "new_api_client_oid": new_api_client_oid,
        "replace_result": replace_result,
        "cancel_response": cancel_response,
        "submit_response": submit_response,
        "submit_error": submit_error,
        "intended_limit_price": intended_limit_price,
        "intended_client_oid": intended_client_oid,
        "evaluated_at": utc_now_iso(),
    }

    if extra:
        payload.update(extra)

    return payload


def store_last_reprice_payload(
    conn,
    action_id: int,
    payload: dict[str, Any],
    *,
    table_name: str = "action_queue",
) -> None:
    conn.execute(
        f"""
        UPDATE {table_name}
        SET last_reprice_payload = ?
        WHERE id = ?
        """,
        (json_dumps_compact(payload), action_id),
    )
    conn.commit()
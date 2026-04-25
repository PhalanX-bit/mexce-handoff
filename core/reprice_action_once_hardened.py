from __future__ import annotations

import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional

from core.ops_lock import acquire_action_lock, release_action_lock
from core.reprice_payloads import build_reprice_payload, store_last_reprice_payload
from core.reprice_service import (
    RepriceServiceConfig,
    evaluate_reprice_need,
    get_action_by_id,
    normalize_api_order_id,
    replace_limit_order_from_action,
    update_action_row,
)


def _result_to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"value": str(obj)}


def _safe_get(source: Dict[str, Any], key: str, default: Any = None) -> Any:
    return source.get(key, default)


def reprice_action_once_hardened(
    *,
    conn: sqlite3.Connection,
    action_id: int,
    target_price: float,
    config: RepriceServiceConfig,
    apply_changes: bool = True,
    require_order_open: Optional[bool] = None,
    lock_timeout_sec: int = 90,
    table_name: str = "action_queue",
) -> Dict[str, Any]:
    """
    Lean hardening wrapper around the existing reprice flow.

    Adds only:
    - per-action DB lock
    - last_reprice_payload persistence
    - clearer last_error on replace-flow exception

    Important:
    - business logic remains in core.reprice_service
    - DB writes for action row still happen via update_action_row(...)
    """

    lock = acquire_action_lock(
        conn,
        action_id,
        lock_timeout_sec=lock_timeout_sec,
        table_name=table_name,
    )
    if not lock.acquired:
        return {
            "ok": True,
            "action_id": action_id,
            "stage": "skip",
            "reason": f"lock_not_acquired:{lock.reason}",
        }

    try:
        row_now = get_action_by_id(action_id)
        old_api_order_id = normalize_api_order_id(row_now.get("api_order_id")) if row_now else None

        effective_require_order_open = (
            bool(config.require_order_open)
            if require_order_open is None
            else bool(require_order_open)
        )

        effective_config = RepriceServiceConfig(
            threshold_bps=float(config.threshold_bps),
            favorable_threshold_bps=config.favorable_threshold_bps,
            adverse_threshold_bps=config.adverse_threshold_bps,
            max_order_age_sec=config.max_order_age_sec,
            require_order_open=effective_require_order_open,
            update_action_queue=bool(config.update_action_queue),
            max_reprice_distance_bps=config.max_reprice_distance_bps,
        )

        decision_raw = evaluate_reprice_need(
            action_id=action_id,
            target_price=float(target_price),
            config=effective_config,
        )
        decision = _result_to_dict(decision_raw)

        if not decision.get("ok"):
            fail_payload = build_reprice_payload(
                action_id=action_id,
                old_api_order_id=old_api_order_id,
                current_price=None,
                target_price=float(target_price),
                drift_bps=None,
                overshoot_kind=None,
                effective_threshold_bps=None,
                order_age_sec=None,
                max_order_age_sec=effective_config.max_order_age_sec,
                stale_by_drift=None,
                stale_by_age=None,
                should_replace=None,
                decision_reason=str(decision.get("reason")),
                apply_changes=apply_changes,
                replace_result="decision_not_ok",
                extra={
                    "decision_stage": decision.get("stage"),
                    "decision": decision,
                },
            )
            store_last_reprice_payload(conn, action_id, fail_payload, table_name=table_name)
            return decision

        current_order_price = _safe_get(decision, "current_order_price")
        drift_bps = _safe_get(decision, "drift_bps")
        overshoot_kind = _safe_get(decision, "overshoot_kind")
        effective_threshold_bps = _safe_get(decision, "effective_threshold_bps")
        order_age_sec = _safe_get(decision, "order_age_sec")
        max_order_age_sec = _safe_get(decision, "max_order_age_sec")
        stale_by_drift = _safe_get(decision, "stale_by_drift")
        stale_by_age = _safe_get(decision, "stale_by_age")
        should_replace = bool(_safe_get(decision, "should_replace", False))
        decision_reason = _safe_get(decision, "decision_reason")
        max_reprice_distance_bps = _safe_get(decision, "max_reprice_distance_bps")
        too_far_by_distance = _safe_get(decision, "too_far_by_distance")

        initial_payload = build_reprice_payload(
            action_id=action_id,
            old_api_order_id=old_api_order_id,
            current_price=current_order_price,
            target_price=float(target_price),
            drift_bps=drift_bps,
            overshoot_kind=overshoot_kind,
            effective_threshold_bps=effective_threshold_bps,
            order_age_sec=order_age_sec,
            max_order_age_sec=max_order_age_sec,
            stale_by_drift=stale_by_drift,
            stale_by_age=stale_by_age,
            should_replace=should_replace,
            decision_reason=decision_reason,
            apply_changes=apply_changes,
            extra={
                "decision": decision,
                "max_reprice_distance_bps": max_reprice_distance_bps,
                "too_far_by_distance": too_far_by_distance,
            },
        )
        store_last_reprice_payload(conn, action_id, initial_payload, table_name=table_name)

        if not should_replace:
            out = dict(decision)
            out["stage"] = "noop"
            out["applied"] = False
            return out

        if should_replace and not apply_changes:
            would_payload = build_reprice_payload(
                action_id=action_id,
                old_api_order_id=old_api_order_id,
                current_price=current_order_price,
                target_price=float(target_price),
                drift_bps=drift_bps,
                overshoot_kind=overshoot_kind,
                effective_threshold_bps=effective_threshold_bps,
                order_age_sec=order_age_sec,
                max_order_age_sec=max_order_age_sec,
                stale_by_drift=stale_by_drift,
                stale_by_age=stale_by_age,
                should_replace=True,
                decision_reason=decision_reason,
                apply_changes=False,
                replace_result="would_replace",
                extra={
                    "decision": decision,
                    "max_reprice_distance_bps": max_reprice_distance_bps,
                    "too_far_by_distance": too_far_by_distance,
                },
            )
            store_last_reprice_payload(conn, action_id, would_payload, table_name=table_name)

            out = dict(decision)
            out["stage"] = "would_replace"
            out["applied"] = False
            return out

        row_before = row_now
        previous_order_id = (
            normalize_api_order_id(row_before.get("api_order_id"))
            if row_before
            else None
        )
        qty_before = row_before.get("qty") if row_before else None

        replace_result_raw = replace_limit_order_from_action(
            action_id=action_id,
            new_price=float(target_price),
            new_qty=float(qty_before) if qty_before not in (None, "") else None,
        )
        replace_result = _result_to_dict(replace_result_raw)

        replace_ok = bool(replace_result.get("ok"))
        stage = "replaced" if replace_ok else str(replace_result.get("stage", "replace_failed"))

        success_or_fail_payload = build_reprice_payload(
            action_id=action_id,
            old_api_order_id=previous_order_id,
            current_price=current_order_price,
            target_price=float(target_price),
            drift_bps=drift_bps,
            overshoot_kind=overshoot_kind,
            effective_threshold_bps=effective_threshold_bps,
            order_age_sec=order_age_sec,
            max_order_age_sec=max_order_age_sec,
            stale_by_drift=stale_by_drift,
            stale_by_age=stale_by_age,
            should_replace=True,
            decision_reason=decision_reason,
            apply_changes=True,
            new_api_order_id=replace_result.get("new_order_id"),
            new_api_client_oid=replace_result.get("client_oid"),
            replace_result=stage,
            cancel_response=(replace_result.get("cancel") or {}).get("response")
            if isinstance(replace_result.get("cancel"), dict)
            else replace_result.get("cancel"),
            submit_response=replace_result.get("submit_response"),
            intended_limit_price=float(target_price),
            intended_client_oid=replace_result.get("client_oid"),
            extra={
                "decision": decision,
                "replace_result_full": replace_result,
                "max_reprice_distance_bps": max_reprice_distance_bps,
                "too_far_by_distance": too_far_by_distance,
            },
        )
        store_last_reprice_payload(conn, action_id, success_or_fail_payload, table_name=table_name)

        if not replace_ok:
            update_action_row(
                action_id,
                last_error=f"replace failed: {stage}",
            )

        out = dict(replace_result)
        out["stage"] = stage
        out["applied"] = replace_ok
        out["previous_order_id"] = previous_order_id
        out["current_order_price"] = current_order_price
        out["target_price"] = float(target_price)
        out["drift_bps"] = drift_bps
        out["threshold_bps"] = effective_threshold_bps
        out["max_reprice_distance_bps"] = max_reprice_distance_bps
        out["too_far_by_distance"] = too_far_by_distance
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

    except Exception as exc:
        row_after_error = get_action_by_id(action_id)
        fallback_old_api_order_id = (
            normalize_api_order_id(row_after_error.get("api_order_id"))
            if row_after_error
            else None
        )

        error_payload = build_reprice_payload(
            action_id=action_id,
            old_api_order_id=fallback_old_api_order_id,
            current_price=None,
            target_price=float(target_price),
            drift_bps=None,
            overshoot_kind=None,
            effective_threshold_bps=None,
            order_age_sec=None,
            max_order_age_sec=config.max_order_age_sec,
            stale_by_drift=None,
            stale_by_age=None,
            should_replace=None,
            decision_reason="exception_during_replace_flow",
            apply_changes=apply_changes,
            replace_result="error",
            submit_error={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )
        store_last_reprice_payload(conn, action_id, error_payload, table_name=table_name)

        update_action_row(
            action_id,
            last_error=f"replace flow error: {type(exc).__name__}: {exc}",
        )

        return {
            "ok": False,
            "action_id": action_id,
            "stage": "error",
            "reason": str(exc),
            "error_type": type(exc).__name__,
        }

    finally:
        if lock.lock_token:
            release_action_lock(
                conn,
                action_id,
                lock.lock_token,
                table_name=table_name,
            )
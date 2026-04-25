from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.order_manager import (
    extract_open_order_price,
    find_open_order,
    get_action_by_id,
    reprice_limit_order_if_needed,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ms_to_utc_datetime(ms: int | float) -> datetime:
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def seconds_since_ms(ms: int | float, now: Optional[datetime] = None) -> float:
    now = now or utc_now()
    created_dt = ms_to_utc_datetime(ms)
    return (now - created_dt).total_seconds()


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


def extract_open_order_age_sec(open_order_item: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    if not isinstance(open_order_item, dict):
        return None

    for key in ("createTime", "updateTime"):
        value = safe_int(open_order_item.get(key))
        if value is not None:
            return seconds_since_ms(value, now=now)

    return None


def compute_price_drift_bps(current_order_price: float, target_price: float) -> float:
    current_order_price = float(current_order_price)
    target_price = float(target_price)

    if target_price == 0:
        raise ValueError("target_price must not be 0")

    return abs(current_order_price - target_price) / abs(target_price) * 10000.0


def classify_overshoot(
    *,
    panel_mode: str,
    side: str,
    current_order_price: float,
    target_price: float,
) -> str:
    """
    Класифицира движението на target_price спрямо current_order_price.

    Възможни стойности:
    - "none"
    - "favorable_up"
    - "favorable_down"
    - "adverse_up"
    - "adverse_down"
    """
    panel_mode = str(panel_mode).upper().strip()
    side = str(side).upper().strip()
    current_order_price = float(current_order_price)
    target_price = float(target_price)

    if target_price == current_order_price:
        return "none"

    direction = "up" if target_price > current_order_price else "down"

    if panel_mode == "OPEN" and side == "SHORT":
        return "favorable_down" if direction == "down" else "adverse_up"

    if panel_mode == "OPEN" and side == "LONG":
        return "favorable_up" if direction == "up" else "adverse_down"

    if panel_mode == "CLOSE" and side == "LONG":
        return "favorable_up" if direction == "up" else "adverse_down"

    if panel_mode == "CLOSE" and side == "SHORT":
        return "favorable_down" if direction == "down" else "adverse_up"

    raise ValueError(f"Unsupported panel_mode/side combination: {panel_mode}/{side}")


def threshold_for_overshoot_kind(
    *,
    overshoot_kind: str,
    default_threshold_bps: float,
    favorable_threshold_bps: float | None = None,
    adverse_threshold_bps: float | None = None,
) -> float:
    default_threshold_bps = float(default_threshold_bps)

    if overshoot_kind.startswith("favorable_") and favorable_threshold_bps is not None:
        return float(favorable_threshold_bps)

    if overshoot_kind.startswith("adverse_") and adverse_threshold_bps is not None:
        return float(adverse_threshold_bps)

    return default_threshold_bps


def is_order_stale_by_drift(
    *,
    current_order_price: float,
    target_price: float,
    threshold_bps: float,
) -> bool:
    drift_bps = compute_price_drift_bps(current_order_price, target_price)
    return drift_bps >= float(threshold_bps)


def is_order_stale_by_age(
    *,
    order_age_sec: float,
    max_order_age_sec: float,
) -> bool:
    return float(order_age_sec) >= float(max_order_age_sec)


@dataclass
class RepriceDecision:
    ok: bool
    action_id: int
    symbol: str
    api_order_id: str | None
    order_open: bool
    current_order_price: float | None
    target_price: float
    drift_bps: float | None
    threshold_bps: float
    effective_threshold_bps: float
    favorable_threshold_bps: float | None
    adverse_threshold_bps: float | None
    order_age_sec: float | None
    max_order_age_sec: float | None
    stale_by_drift: bool
    stale_by_age: bool
    overshoot_kind: str
    should_replace: bool
    decision_reason: str
    open_order_item: Dict[str, Any] | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "action_id": self.action_id,
            "symbol": self.symbol,
            "api_order_id": self.api_order_id,
            "order_open": self.order_open,
            "current_order_price": self.current_order_price,
            "target_price": self.target_price,
            "drift_bps": self.drift_bps,
            "threshold_bps": self.threshold_bps,
            "effective_threshold_bps": self.effective_threshold_bps,
            "favorable_threshold_bps": self.favorable_threshold_bps,
            "adverse_threshold_bps": self.adverse_threshold_bps,
            "order_age_sec": self.order_age_sec,
            "max_order_age_sec": self.max_order_age_sec,
            "stale_by_drift": self.stale_by_drift,
            "stale_by_age": self.stale_by_age,
            "overshoot_kind": self.overshoot_kind,
            "should_replace": self.should_replace,
            "decision_reason": self.decision_reason,
            "open_order_item": self.open_order_item,
        }


def evaluate_reprice_decision(
    *,
    action_id: int,
    target_price: float,
    threshold_bps: float = 2.0,
    favorable_threshold_bps: float | None = None,
    adverse_threshold_bps: float | None = None,
    max_order_age_sec: float | None = None,
    now: Optional[datetime] = None,
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
    panel_mode = str(row.get("panel_mode") or "").upper().strip()
    side = str(row.get("side") or "").upper().strip()
    api_order_id = row.get("api_order_id")

    if api_order_id in (None, ""):
        return {
            "ok": False,
            "stage": "precheck",
            "reason": "missing_api_order_id",
            "action_id": action_id,
            "symbol": symbol,
        }

    api_order_id = str(api_order_id)
    target_price = float(target_price)
    threshold_bps = float(threshold_bps)

    open_order_item = find_open_order(symbol=symbol, order_id=api_order_id)
    order_open = open_order_item is not None

    if not order_open:
        return {
            "ok": True,
            "stage": "precheck",
            "reason": "order_not_open",
            "action_id": action_id,
            "symbol": symbol,
            "api_order_id": api_order_id,
            "target_price": target_price,
            "should_replace": False,
        }

    current_order_price = extract_open_order_price(open_order_item)
    if current_order_price is None:
        return {
            "ok": False,
            "stage": "inspect_open_order",
            "reason": "missing_order_price",
            "action_id": action_id,
            "symbol": symbol,
            "api_order_id": api_order_id,
            "open_order_item": open_order_item,
        }

    overshoot_kind = classify_overshoot(
        panel_mode=panel_mode,
        side=side,
        current_order_price=current_order_price,
        target_price=target_price,
    )

    effective_threshold_bps = threshold_for_overshoot_kind(
        overshoot_kind=overshoot_kind,
        default_threshold_bps=threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
    )

    drift_bps = compute_price_drift_bps(current_order_price, target_price)
    stale_by_drift = is_order_stale_by_drift(
        current_order_price=current_order_price,
        target_price=target_price,
        threshold_bps=effective_threshold_bps,
    )

    order_age_sec = extract_open_order_age_sec(open_order_item, now=now)
    stale_by_age = False
    if max_order_age_sec is not None and order_age_sec is not None:
        stale_by_age = is_order_stale_by_age(
            order_age_sec=order_age_sec,
            max_order_age_sec=max_order_age_sec,
        )

    should_replace = stale_by_drift or stale_by_age

    if stale_by_drift and stale_by_age:
        decision_reason = "stale_by_drift_and_age"
    elif stale_by_drift:
        decision_reason = "stale_by_drift"
    elif stale_by_age:
        decision_reason = "stale_by_age"
    else:
        decision_reason = "within_threshold_and_age"

    decision = RepriceDecision(
        ok=True,
        action_id=action_id,
        symbol=symbol,
        api_order_id=api_order_id,
        order_open=order_open,
        current_order_price=current_order_price,
        target_price=target_price,
        drift_bps=drift_bps,
        threshold_bps=threshold_bps,
        effective_threshold_bps=effective_threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        order_age_sec=order_age_sec,
        max_order_age_sec=max_order_age_sec,
        stale_by_drift=stale_by_drift,
        stale_by_age=stale_by_age,
        overshoot_kind=overshoot_kind,
        should_replace=should_replace,
        decision_reason=decision_reason,
        open_order_item=open_order_item,
    )
    return decision.to_dict()


def apply_reprice_decision(
    *,
    action_id: int,
    target_price: float,
    threshold_bps: float = 2.0,
    favorable_threshold_bps: float | None = None,
    adverse_threshold_bps: float | None = None,
    max_order_age_sec: float | None = None,
    require_order_open: bool = True,
    update_action_queue: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    decision = evaluate_reprice_decision(
        action_id=action_id,
        target_price=target_price,
        threshold_bps=threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_order_age_sec=max_order_age_sec,
        now=now,
    )

    if not decision.get("ok"):
        return decision

    if decision.get("reason") == "order_not_open":
        decision["stage"] = "noop"
        decision["applied"] = False
        return decision

    if not decision.get("should_replace"):
        decision["stage"] = "noop"
        decision["applied"] = False
        return decision

    result = reprice_limit_order_if_needed(
        action_id=action_id,
        target_price=target_price,
        threshold_bps=float(decision["effective_threshold_bps"]),
        require_order_open=require_order_open,
        update_action_queue=update_action_queue,
    )

    result["policy_decision"] = decision
    result["applied"] = bool(result.get("ok"))
    return result
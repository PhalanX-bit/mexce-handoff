from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.ops_loop import OpsLoopConfig, run_ops_pass
from core.reconcile_service import reconcile_open_actions
from core.reprice_service import RepriceServiceConfig
from core.reprice_worker import RepriceWorkerConfig, run_reprice_pass
from core.streamlit_services.execution_policy_service import (
    build_market_target_price_resolver,
)


def build_default_target_price_resolver() -> Callable[[Dict[str, Any]], Optional[float]]:
    def _resolver(action_row: Dict[str, Any]) -> Optional[float]:
        value = action_row.get("limit_price")
        if value in (None, ""):
            return None
        return float(value)

    return _resolver


def build_controls_reprice_config(
    *,
    statuses: tuple[str, ...],
    order_kinds: tuple[str, ...],
    max_items: int,
    symbols: Optional[tuple[str, ...]],
    created_by_prefixes: Optional[tuple[str, ...]],
    include_null_reconcile_state: bool,
    threshold_bps: float,
    favorable_threshold_bps: float,
    adverse_threshold_bps: float,
    max_reprice_distance_bps: Optional[float],
    require_order_open: bool,
) -> RepriceWorkerConfig:
    return RepriceWorkerConfig(
        service=RepriceServiceConfig(
            threshold_bps=float(threshold_bps),
            favorable_threshold_bps=float(favorable_threshold_bps),
            adverse_threshold_bps=float(adverse_threshold_bps),
            max_order_age_sec=None,
            require_order_open=bool(require_order_open),
            update_action_queue=True,
            max_reprice_distance_bps=(
                None if max_reprice_distance_bps is None else float(max_reprice_distance_bps)
            ),
        ),
        statuses=statuses,
        order_kinds=order_kinds,
        max_items=int(max_items),
        poll_interval_sec=2.0,
        symbols=symbols,
        created_by_prefixes=created_by_prefixes,
        include_null_reconcile_state=bool(include_null_reconcile_state),
    )


def build_controls_target_price_resolver(
    con,
    *,
    resolver_mode: str,
    open_offset_bps: float,
    close_offset_bps: float,
    prefer_mark_price: bool,
    apply_tick_rounding: bool,
) -> Callable[[Dict[str, Any]], Optional[float]]:
    mode = str(resolver_mode or "").strip().lower()

    if mode == "market":
        return build_market_target_price_resolver(
            con,
            open_offset_bps=float(open_offset_bps),
            close_offset_bps=float(close_offset_bps),
            prefer_mark_price=bool(prefer_mark_price),
            apply_tick_rounding=bool(apply_tick_rounding),
        )

    return build_default_target_price_resolver()


def run_controls_reprice_pass(
    *,
    target_price_resolver: Callable[[Dict[str, Any]], Optional[float]],
    statuses: tuple[str, ...],
    order_kinds: tuple[str, ...],
    max_items: int,
    symbols: Optional[tuple[str, ...]],
    created_by_prefixes: Optional[tuple[str, ...]],
    include_null_reconcile_state: bool,
    threshold_bps: float,
    favorable_threshold_bps: float,
    adverse_threshold_bps: float,
    max_reprice_distance_bps: Optional[float],
    require_order_open: bool,
    apply_changes: bool,
    verbose: bool,
) -> Dict[str, Any]:
    config = build_controls_reprice_config(
        statuses=statuses,
        order_kinds=order_kinds,
        max_items=max_items,
        symbols=symbols,
        created_by_prefixes=created_by_prefixes,
        include_null_reconcile_state=include_null_reconcile_state,
        threshold_bps=threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_reprice_distance_bps=max_reprice_distance_bps,
        require_order_open=require_order_open,
    )

    return run_reprice_pass(
        target_price_resolver=target_price_resolver,
        config=config,
        apply_changes=bool(apply_changes),
        verbose=bool(verbose),
    )


def run_controls_reconcile_pass(
    *,
    statuses: tuple[str, ...],
    max_items: int,
    symbols: Optional[tuple[str, ...]],
    created_by_prefixes: Optional[tuple[str, ...]],
    include_null_reconcile_state: bool,
    verbose: bool,
) -> Dict[str, Any]:
    return reconcile_open_actions(
        statuses=statuses,
        max_items=int(max_items),
        update_action_queue=True,
        verbose=bool(verbose),
        symbols=symbols,
        created_by_prefixes=created_by_prefixes,
        include_null_reconcile_state=bool(include_null_reconcile_state),
    )


def run_controls_ops_pass(
    *,
    target_price_resolver: Callable[[Dict[str, Any]], Optional[float]],
    statuses: tuple[str, ...],
    order_kinds: tuple[str, ...],
    max_items: int,
    symbols: Optional[tuple[str, ...]],
    created_by_prefixes: Optional[tuple[str, ...]],
    include_null_reconcile_state: bool,
    threshold_bps: float,
    favorable_threshold_bps: float,
    adverse_threshold_bps: float,
    max_reprice_distance_bps: Optional[float],
    require_order_open: bool,
    apply_reprice_changes: bool,
    verbose: bool,
) -> Dict[str, Any]:
    reprice_config = build_controls_reprice_config(
        statuses=statuses,
        order_kinds=order_kinds,
        max_items=max_items,
        symbols=symbols,
        created_by_prefixes=created_by_prefixes,
        include_null_reconcile_state=include_null_reconcile_state,
        threshold_bps=threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_reprice_distance_bps=max_reprice_distance_bps,
        require_order_open=require_order_open,
    )

    ops_config = OpsLoopConfig(
        reprice=reprice_config,
        reconcile_statuses=statuses,
        reconcile_max_items=int(max_items),
        reconcile_update_action_queue=True,
        loop_interval_sec=2.0,
        symbols=symbols,
        created_by_prefixes=created_by_prefixes,
        include_null_reconcile_state=bool(include_null_reconcile_state),
    )

    return run_ops_pass(
        target_price_resolver=target_price_resolver,
        config=ops_config,
        apply_reprice_changes=bool(apply_reprice_changes),
        verbose=bool(verbose),
    )
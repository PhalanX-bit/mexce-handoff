from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from core.reprice_service import RepriceServiceConfig
from core.reprice_worker import RepriceWorkerConfig, run_reprice_loop, run_reprice_pass
from core.reconcile_service import reconcile_open_actions


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"))


@dataclass
class OpsLoopConfig:
    reprice: RepriceWorkerConfig
    reconcile_statuses: tuple[str, ...] = ("DONE",)
    reconcile_max_items: int = 100
    reconcile_update_action_queue: bool = True
    loop_interval_sec: float = 2.0

    # NEW: shared scope filters
    symbols: Optional[tuple[str, ...]] = None
    created_by_prefixes: Optional[tuple[str, ...]] = None
    only_reconcile_states: Optional[tuple[str, ...]] = None
    include_null_reconcile_state: bool = True
    min_created_at: Optional[str] = None
    max_age_hours: Optional[float] = None


def run_ops_pass(
    *,
    target_price_resolver: Callable[[Dict[str, Any]], Optional[float]],
    config: OpsLoopConfig,
    apply_reprice_changes: bool = True,
    verbose: bool = True,
) -> Dict[str, Any]:
    started_at = utc_now_iso()

    # apply shared scope to reprice config
    reprice_cfg = RepriceWorkerConfig(
        service=config.reprice.service,
        statuses=config.reprice.statuses,
        order_kinds=config.reprice.order_kinds,
        max_items=config.reprice.max_items,
        poll_interval_sec=config.reprice.poll_interval_sec,
        symbols=config.symbols if config.symbols is not None else config.reprice.symbols,
        created_by_prefixes=(
            config.created_by_prefixes
            if config.created_by_prefixes is not None
            else config.reprice.created_by_prefixes
        ),
        only_reconcile_states=(
            config.only_reconcile_states
            if config.only_reconcile_states is not None
            else config.reprice.only_reconcile_states
        ),
        include_null_reconcile_state=config.include_null_reconcile_state,
        min_created_at=config.min_created_at if config.min_created_at is not None else config.reprice.min_created_at,
        max_age_hours=config.max_age_hours if config.max_age_hours is not None else config.reprice.max_age_hours,
    )

    reprice_result = run_reprice_pass(
        target_price_resolver=target_price_resolver,
        config=reprice_cfg,
        apply_changes=apply_reprice_changes,
        verbose=verbose,
    )

    reconcile_result = reconcile_open_actions(
        statuses=config.reconcile_statuses,
        max_items=int(config.reconcile_max_items),
        update_action_queue=bool(config.reconcile_update_action_queue),
        verbose=verbose,
        symbols=config.symbols,
        created_by_prefixes=config.created_by_prefixes,
        only_reconcile_states=config.only_reconcile_states,
        include_null_reconcile_state=config.include_null_reconcile_state,
        min_created_at=config.min_created_at,
        max_age_hours=config.max_age_hours,
    )

    finished_at = utc_now_iso()

    return {
        "ok": True,
        "stage": "ops_pass_done",
        "started_at": started_at,
        "finished_at": finished_at,
        "apply_reprice_changes": bool(apply_reprice_changes),
        "scope": {
            "symbols": config.symbols,
            "created_by_prefixes": config.created_by_prefixes,
            "only_reconcile_states": config.only_reconcile_states,
            "include_null_reconcile_state": config.include_null_reconcile_state,
            "min_created_at": config.min_created_at,
            "max_age_hours": config.max_age_hours,
        },
        "reprice_result": reprice_result,
        "reconcile_result": reconcile_result,
    }


def run_ops_loop(
    *,
    target_price_resolver: Callable[[Dict[str, Any]], Optional[float]],
    config: OpsLoopConfig,
    apply_reprice_changes: bool = True,
    max_passes: int = 10,
    verbose: bool = True,
) -> Dict[str, Any]:
    max_passes = int(max_passes)
    history = []

    for pass_no in range(1, max_passes + 1):
        result = run_ops_pass(
            target_price_resolver=target_price_resolver,
            config=config,
            apply_reprice_changes=apply_reprice_changes,
            verbose=verbose,
        )
        result["pass_no"] = pass_no
        history.append(result)

        if verbose:
            print(
                safe_json(
                    {
                        "pass_no": pass_no,
                        "stage": result["stage"],
                        "reprice_candidates": result["reprice_result"].get("candidates"),
                        "reconcile_candidates": result["reconcile_result"].get("candidates"),
                    }
                )
            )

        if pass_no < max_passes:
            time.sleep(float(config.loop_interval_sec))

    return {
        "ok": True,
        "stage": "ops_loop_done",
        "passes_completed": max_passes,
        "history": history,
        "finished_at": utc_now_iso(),
    }


def default_noop_target_price_resolver(action_row: Dict[str, Any]) -> Optional[float]:
    value = action_row.get("limit_price")
    if value in (None, ""):
        return None
    return float(value)


if __name__ == "__main__":
    reprice_cfg = RepriceWorkerConfig(
        service=RepriceServiceConfig(
            threshold_bps=2.0,
            favorable_threshold_bps=1.0,
            adverse_threshold_bps=10.0,
            max_order_age_sec=None,
            require_order_open=True,
            update_action_queue=True,
        ),
        statuses=("DONE",),
        order_kinds=("LIMIT", "POST_ONLY"),
        max_items=50,
        poll_interval_sec=2.0,
    )

    ops_cfg = OpsLoopConfig(
        reprice=reprice_cfg,
        reconcile_statuses=("DONE",),
        reconcile_max_items=50,
        reconcile_update_action_queue=True,
        loop_interval_sec=2.0,
        symbols=("BTC/USDT:USDT",),
        max_age_hours=6.0,
        only_reconcile_states=("OPEN",),
        include_null_reconcile_state=True,
    )

    out = run_ops_pass(
        target_price_resolver=default_noop_target_price_resolver,
        config=ops_cfg,
        apply_reprice_changes=False,
        verbose=True,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
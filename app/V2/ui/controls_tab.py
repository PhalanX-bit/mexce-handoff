from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from core.streamlit_services.controls_service import (
    build_controls_target_price_resolver,
    run_controls_ops_pass,
    run_controls_reconcile_pass,
    run_controls_reprice_pass,
)
from core.streamlit_services.symbol_picker_service import (
    load_futures_symbol_options,
    resolve_default_symbol_index,
)
from core.streamlit_services.table_columns_service import project_df_columns
from core.symbol_utils import canonical_futures_symbol


def _pretty(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def _build_scope_inputs(con):
    symbol_options = ["ALL"] + load_futures_symbol_options(con)

    c1, c2, c3 = st.columns(3)

    default_index = 0
    if len(symbol_options) > 1:
        default_index = resolve_default_symbol_index(symbol_options[1:], "BTC/USDT:USDT") + 1

    symbol = c1.selectbox(
        "Symbol filter",
        options=symbol_options,
        index=default_index,
        key="v2_controls_symbol",
    )

    created_by_prefix = c2.text_input(
        "created_by prefix (optional)",
        value="",
        key="v2_controls_created_by",
    ).strip()

    max_items = c3.number_input(
        "Max items",
        min_value=1,
        value=20,
        step=1,
        key="v2_controls_max_items",
    )

    symbol = "" if symbol == "ALL" else str(symbol).strip()
    return symbol, created_by_prefix, int(max_items)


def _build_threshold_inputs():
    d1, d2, d3, d4 = st.columns(4)
    threshold_bps = d1.number_input(
        "Base threshold bps",
        min_value=0.1,
        value=2.0,
        step=0.1,
        key="v2_controls_threshold_bps",
    )
    favorable_threshold_bps = d2.number_input(
        "Favorable threshold bps",
        min_value=0.1,
        value=1.0,
        step=0.1,
        key="v2_controls_favorable_threshold_bps",
    )
    adverse_threshold_bps = d3.number_input(
        "Adverse threshold bps",
        min_value=0.1,
        value=10.0,
        step=0.1,
        key="v2_controls_adverse_threshold_bps",
    )
    max_reprice_distance_bps = d4.number_input(
        "Max reprice distance bps",
        min_value=1.0,
        value=100.0,
        step=1.0,
        key="v2_controls_max_reprice_distance_bps",
    )

    return (
        float(threshold_bps),
        float(favorable_threshold_bps),
        float(adverse_threshold_bps),
        float(max_reprice_distance_bps),
    )


def _build_toggle_inputs():
    e1, e2, e3 = st.columns(3)
    require_order_open = e1.checkbox(
        "Require order open",
        value=True,
        key="v2_controls_require_order_open",
    )
    include_null_reconcile_state = e2.checkbox(
        "Include null reconcile_state",
        value=True,
        key="v2_controls_include_null_reconcile_state",
    )
    verbose = e3.checkbox(
        "Verbose",
        value=True,
        key="v2_controls_verbose",
    )

    return bool(require_order_open), bool(include_null_reconcile_state), bool(verbose)


def _build_order_kind_inputs() -> tuple[str, ...]:
    f1, f2, f3 = st.columns(3)
    order_kind_limit = f1.checkbox(
        "Include LIMIT",
        value=True,
        key="v2_controls_order_kind_limit",
    )
    order_kind_post_only = f2.checkbox(
        "Include POST_ONLY",
        value=True,
        key="v2_controls_order_kind_post_only",
    )
    order_kind_market = f3.checkbox(
        "Include MARKET",
        value=False,
        key="v2_controls_order_kind_market",
    )

    order_kinds: list[str] = []
    if order_kind_limit:
        order_kinds.append("LIMIT")
    if order_kind_post_only:
        order_kinds.append("POST_ONLY")
    if order_kind_market:
        order_kinds.append("MARKET")

    return tuple(order_kinds)


def _build_resolver_inputs():
    st.write("### Target resolver")
    c1, c2, c3 = st.columns(3)

    resolver_mode = c1.selectbox(
        "Resolver mode",
        ["current_limit_price", "market"],
        index=0,
        key="v2_controls_resolver_mode",
    )

    prefer_mark_price = c2.checkbox(
        "Prefer mark price",
        value=False,
        key="v2_controls_prefer_mark_price",
    )

    apply_tick_rounding = c3.checkbox(
        "Apply tick-size rounding",
        value=True,
        key="v2_controls_apply_tick_rounding",
    )

    c3.caption("Market mode uses live market API-derived price.")

    d1, d2 = st.columns(2)
    open_offset_bps = d1.number_input(
        "OPEN offset bps",
        min_value=0.0,
        value=5.0,
        step=0.5,
        key="v2_controls_open_offset_bps",
    )
    close_offset_bps = d2.number_input(
        "CLOSE offset bps",
        min_value=0.0,
        value=5.0,
        step=0.5,
        key="v2_controls_close_offset_bps",
    )

    e1, _ = st.columns(2)
    preview_only_open_orders = e1.checkbox(
        "Only rows with open order (preview)",
        value=True,
        key="v2_controls_preview_only_open_orders",
    )

    return (
        str(resolver_mode),
        bool(prefer_mark_price),
        bool(apply_tick_rounding),
        float(open_offset_bps),
        float(close_offset_bps),
        bool(preview_only_open_orders),
    )


def _render_result(title: str, result) -> None:
    st.success(title)
    st.code(_pretty(result), language="json")


def _build_reconcile_preview_df(result):
    rows = []
    for item in (result or {}).get("results", []):
        fill_registry = item.get("fill_registry") or {}
        fill_result = fill_registry.get("fill_result") or {}

        rows.append(
            {
                "action_id": item.get("action_id"),
                "symbol": item.get("symbol"),
                "api_order_id": item.get("api_order_id"),
                "lifecycle_state": item.get("lifecycle_state"),
                "lifecycle_reason": item.get("lifecycle_reason"),
                "manual_backfill_hint": item.get("manual_backfill_hint"),
                "deal_qty": item.get("deal_qty"),
                "resolved_avg_price": item.get("resolved_avg_price"),
                "queue_updated": item.get("queue_updated"),
                "ledger_logged": item.get("ledger_logged"),
                "fill_applied": fill_registry.get("applied"),
                "fill_reason": fill_registry.get("reason"),
                "fill_panel_mode": fill_registry.get("panel_mode"),
                "fill_price": fill_registry.get("fill_price"),
                "fill_duplicate": fill_result.get("duplicate"),
                "fill_lot_id": fill_result.get("lot_id"),
                "fill_matched_close_qty": fill_result.get("matched_close_qty"),
                "fill_unmatched_close_qty": fill_result.get("unmatched_close_qty"),
            }
        )

    if not rows:
        return None

    return pd.DataFrame(rows)


def _build_preview_df_from_reprice_result(result, *, only_open_orders: bool):
    rows = []
    for item in (result or {}).get("results", []):
        if only_open_orders and not bool(item.get("order_open")):
            continue

        display_symbol = (
            item.get("canonical_symbol")
            or canonical_futures_symbol(item.get("symbol"))
            or item.get("symbol")
        )

        rows.append(
            {
                "action_id": item.get("action_id"),
                "symbol": display_symbol,
                "api_order_id": item.get("api_order_id"),
                "order_open": item.get("order_open"),
                "current_order_price": item.get("current_order_price"),
                "target_price": item.get("target_price"),
                "drift_bps": item.get("drift_bps"),
                "effective_threshold_bps": item.get("effective_threshold_bps"),
                "max_reprice_distance_bps": item.get("max_reprice_distance_bps"),
                "too_far_by_distance": item.get("too_far_by_distance"),
                "should_replace": item.get("should_replace"),
                "decision_reason": item.get("decision_reason"),
                "stage": item.get("stage"),
                "applied": item.get("applied"),
                "ledger_logged": item.get("ledger_logged"),
            }
        )

    if not rows:
        return None

    return pd.DataFrame(rows)


def render_controls_tab(con) -> None:
    st.subheader("Controls")
    st.caption("Safe manual controls for reprice / reconcile / ops passes.")

    symbol, created_by_prefix, max_items = _build_scope_inputs(con)
    threshold_bps, favorable_threshold_bps, adverse_threshold_bps, max_reprice_distance_bps = _build_threshold_inputs()
    require_order_open, include_null_reconcile_state, verbose = _build_toggle_inputs()
    order_kinds = _build_order_kind_inputs()

    if not order_kinds:
        st.warning("At least one order kind must be selected.")
        return

    (
        resolver_mode,
        prefer_mark_price,
        apply_tick_rounding,
        open_offset_bps,
        close_offset_bps,
        preview_only_open_orders,
    ) = _build_resolver_inputs()

    statuses = ("DONE",)
    symbols = (symbol,) if symbol else None
    created_by_prefixes = (created_by_prefix,) if created_by_prefix else None

    target_price_resolver = build_controls_target_price_resolver(
        con,
        resolver_mode=resolver_mode,
        open_offset_bps=open_offset_bps,
        close_offset_bps=close_offset_bps,
        prefer_mark_price=prefer_mark_price,
        apply_tick_rounding=apply_tick_rounding,
    )

    st.write("### Resolver preview")
    if st.button("Preview resolved target prices", key="v2_preview_resolved_target_prices"):
        try:
            preview_result = run_controls_reprice_pass(
                target_price_resolver=target_price_resolver,
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
                apply_changes=False,
                verbose=verbose,
            )
            preview_df = _build_preview_df_from_reprice_result(
                preview_result,
                only_open_orders=preview_only_open_orders,
            )

            if preview_df is not None and not preview_df.empty:
                st.dataframe(
                    project_df_columns(preview_df, "controls_preview"),
                    width="stretch",
                    hide_index=True,
                )
            else:
                if preview_only_open_orders:
                    st.info("No matching preview rows with open orders.")
                else:
                    st.info("No matching preview rows.")
        except Exception as exc:
            st.error(f"Preview failed: {exc}")

    st.write("### Reprice controls")
    r1, r2 = st.columns(2)

    if r1.button("Run dry reprice pass", key="v2_run_dry_reprice_pass"):
        try:
            result = run_controls_reprice_pass(
                target_price_resolver=target_price_resolver,
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
                apply_changes=False,
                verbose=verbose,
            )
            _render_result("Dry reprice pass completed.", result)
        except Exception as exc:
            st.error(f"Dry reprice pass failed: {exc}")

    if r2.button("Run live reprice pass", key="v2_run_live_reprice_pass"):
        try:
            result = run_controls_reprice_pass(
                target_price_resolver=target_price_resolver,
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
                apply_changes=True,
                verbose=verbose,
            )
            _render_result("Live reprice pass completed.", result)
        except Exception as exc:
            st.error(f"Live reprice pass failed: {exc}")

    st.write("### Reconcile controls")
    g1, _ = st.columns(2)

    if g1.button("Run reconcile pass", key="v2_run_reconcile_pass"):
        try:
            result = run_controls_reconcile_pass(
                statuses=statuses,
                max_items=max_items,
                symbols=symbols,
                created_by_prefixes=created_by_prefixes,
                include_null_reconcile_state=include_null_reconcile_state,
                verbose=verbose,
            )
            preview_df = _build_reconcile_preview_df(result)
            if preview_df is not None and not preview_df.empty:
                st.dataframe(
                    preview_df,
                    width="stretch",
                    hide_index=True,
                )
            _render_result("Reconcile pass completed.", result)
        except Exception as exc:
            st.error(f"Reconcile pass failed: {exc}")

    st.write("### Combined ops controls")
    h1, h2 = st.columns(2)

    if h1.button("Run dry ops pass", key="v2_run_dry_ops_pass"):
        try:
            result = run_controls_ops_pass(
                target_price_resolver=target_price_resolver,
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
                apply_reprice_changes=False,
                verbose=verbose,
            )
            _render_result("Dry ops pass completed.", result)
        except Exception as exc:
            st.error(f"Dry ops pass failed: {exc}")

    if h2.button("Run live ops pass", key="v2_run_live_ops_pass"):
        try:
            result = run_controls_ops_pass(
                target_price_resolver=target_price_resolver,
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
                apply_reprice_changes=True,
                verbose=verbose,
            )
            _render_result("Live ops pass completed.", result)
        except Exception as exc:
            st.error(f"Live ops pass failed: {exc}")

    with st.expander("Notes", expanded=False):
        st.write(
            "- current_limit_price: uses the row's existing limit_price.\n"
            "- market: derives target from live market API and OPEN/CLOSE side logic.\n"
            "- Apply tick-size rounding: rounds resolved target prices to the valid market step when available.\n"
            "- Preview is intentionally projected to the most useful columns only.\n"
            "- Symbol filter = ALL means no symbol restriction."
        )

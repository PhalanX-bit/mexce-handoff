from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.strategy_simulation_service import (
    build_live_market_seed,
    simulate_market_scenario_pack,
    simulate_strategy_market,
)
from core.streamlit_services.table_columns_service import project_df_columns


def _render_summary(summary: dict) -> None:
    st.write("### Simulation summary")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scenario", str(summary.get("scenario") or "—"))
    c2.metric("Steps", int(summary.get("steps") or 0))
    c3.metric("Start price", f"{float(summary.get('start_price') or 0.0):.6f}")
    c4.metric("Final price", f"{float(summary.get('final_price') or 0.0):.6f}")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Final equity", f"{float(summary.get('final_equity') or 0.0):.4f}")
    d2.metric("Peak equity", f"{float(summary.get('peak_equity') or 0.0):.4f}")
    d3.metric("Min equity", f"{float(summary.get('min_equity') or 0.0):.4f}")
    d4.metric("Max drawdown %", f"{float(summary.get('max_drawdown_pct') or 0.0):.2f}")

    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Max margin ratio %", f"{float(summary.get('max_margin_ratio_est_pct') or 0.0):.2f}")
    e2.metric("Max gross contracts", f"{float(summary.get('max_gross_contracts') or 0.0):.2f}")
    e3.metric("Actions count", int(summary.get("actions_count") or 0))
    e4.metric("Warnings", int(summary.get("warning_count") or 0))

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Fatal count", int(summary.get("fatal_count") or 0))
    f2.metric("Max imbalance", f"{float(summary.get('max_imbalance_ratio') or 0.0):.2f}")
    f3.metric("First warning step", "—" if summary.get("first_warning_step") is None else int(summary["first_warning_step"]))
    f4.metric("First fatal step", "—" if summary.get("first_fatal_step") is None else int(summary["first_fatal_step"]))

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Final long qty", f"{float(summary.get('final_long_qty') or 0.0):.2f}")
    g2.metric("Final short qty", f"{float(summary.get('final_short_qty') or 0.0):.2f}")
    g3.metric("First warning reason", str(summary.get("first_warning_reason") or "—"))
    g4.metric("First fatal reason", str(summary.get("first_fatal_reason") or "—"))

    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Seed long qty", f"{float(summary.get('seed_long_qty') or 0.0):.2f}")
    h2.metric("Seed short qty", f"{float(summary.get('seed_short_qty') or 0.0):.2f}")
    h3.metric("Seed long entry", f"{float(summary.get('seed_long_entry') or 0.0):.6f}")
    h4.metric("Seed short entry", f"{float(summary.get('seed_short_entry') or 0.0):.6f}")

    i1, i2, i3 = st.columns(3)
    i1.metric("Gross cap enabled", "YES" if summary.get("gross_cap_enabled") else "NO")
    i2.metric("Gross cap contracts", f"{float(summary.get('gross_cap_contracts') or 0.0):.2f}")
    i3.metric("Gross cap block events", int(summary.get("gross_cap_block_events") or 0))


def _render_dataframe_block(title: str, df: pd.DataFrame, column_profile: str | None = None) -> None:
    st.write(f"### {title}")
    if df.empty:
        st.info(f"No rows for {title.lower()}.")
        return

    display_df = project_df_columns(df, column_profile) if column_profile else df
    st.dataframe(display_df, width="stretch", hide_index=True)


def _render_live_seed(seed: dict) -> None:
    st.write("### Current live seed")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current price", f"{float(seed.get('current_price') or 0.0):.6f}" if seed.get("current_price") else "-")
    c2.metric("Position state", str(seed.get("position_state") or "-"))
    c3.metric("Gross contracts", f"{float(seed.get('gross_contracts') or 0.0):.2f}")
    c4.metric("Net contracts", f"{float(seed.get('net_contracts') or 0.0):.2f}")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Long qty", f"{float(seed.get('long_qty') or 0.0):.2f}")
    d2.metric("Short qty", f"{float(seed.get('short_qty') or 0.0):.2f}")
    d3.metric("Long entry", f"{float(seed.get('long_entry') or 0.0):.6f}")
    d4.metric("Short entry", f"{float(seed.get('short_entry') or 0.0):.6f}")

    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Open lots", int(seed.get("open_lots_count") or 0))
    e2.metric("Eligible lots", int(seed.get("eligible_lots_count") or 0))
    e3.metric("Eligible qty", f"{float(seed.get('eligible_lots_qty') or 0.0):.2f}")
    e4.metric("Ticker ts", str(seed.get("ticker_ts") or "-"))

    f1, f2, f3 = st.columns(3)
    f1.metric("Live equity", f"{float(seed.get('equity') or 0.0):.4f}")
    f2.metric("Live free margin", f"{float(seed.get('free_margin') or 0.0):.4f}")
    f3.metric("Account ts", str(seed.get("account_ts") or "-"))


def _render_scenario_pack(pack: dict) -> None:
    comparison_df = pack.get("comparison_df")
    best = pack.get("best_scenario") or {}
    worst = pack.get("worst_scenario") or {}

    st.write("### Three-scenario forecast")
    if comparison_df is None or comparison_df.empty:
        st.info("No scenario comparison available.")
        return

    st.caption(
        f"Effective starting capital: {float(pack.get('effective_starting_capital') or 0.0):.4f} | "
        f"contract value multiplier: {float(pack.get('effective_contract_value_multiplier') or 0.0):.6f}"
    )

    c1, c2 = st.columns(2)
    with c1:
        st.success(
            f"Best scenario: {best.get('scenario_label') or '-'} | "
            f"PnL={float(best.get('projected_pnl') or 0.0):.4f} | "
            f"risk={best.get('risk_label') or '-'}"
        )
    with c2:
        st.warning(
            f"Most stressed scenario: {worst.get('scenario_label') or '-'} | "
            f"PnL={float(worst.get('projected_pnl') or 0.0):.4f} | "
            f"risk={worst.get('risk_label') or '-'}"
        )

    st.dataframe(comparison_df, width="stretch", hide_index=True)


def render_strategy_simulation_tab(con) -> None:
    st.subheader("Strategy Simulation")
    st.caption(
        "Offline simulation over synthetic price paths to identify stress regimes, "
        "drawdowns, gross exposure expansion, warnings, fatal moments, and gross-cap blocking."
    )

    c1, c2, c3 = st.columns(3)
    symbol = c1.text_input("Symbol", value="ADA/USDT:USDT", key="v2_sim_symbol").strip()
    initial_side = c2.selectbox("Initial side", ["LONG", "SHORT"], index=1, key="v2_sim_initial_side")
    scenario = c3.selectbox(
        "Scenario",
        [
            "flat",
            "trend_up",
            "trend_down",
            "range",
            "grind_down_bounce",
            "shock_down_recover",
            "shock_up_revert",
        ],
        index=2,
        key="v2_sim_scenario",
    )

    d1, d2, d3, d4 = st.columns(4)
    start_price = d1.number_input("Start price", min_value=0.000001, value=0.2433, step=0.0001, format="%.6f", key="v2_sim_start_price")
    steps = d2.number_input("Steps", min_value=10, max_value=2000, value=200, step=10, key="v2_sim_steps")
    drift_pct_per_step = d3.number_input(
        "Drift % per step",
        min_value=0.0,
        value=0.20,
        step=0.05,
        format="%.4f",
        key="v2_sim_drift_pct",
    )
    oscillation_pct = d4.number_input(
        "Oscillation % per step",
        min_value=0.0,
        value=0.35,
        step=0.05,
        format="%.4f",
        key="v2_sim_oscillation_pct",
    )

    e1, e2, e3, e4 = st.columns(4)
    starting_capital = e1.number_input("Starting capital", min_value=1.0, value=100.0, step=10.0, key="v2_sim_starting_capital")
    secured_capital = e2.number_input("Secured capital", min_value=0.0, value=0.0, step=10.0, key="v2_sim_secured_capital")
    leverage = e3.number_input("Leverage", min_value=1.0, value=300.0, step=1.0, key="v2_sim_leverage")
    contract_step = e4.number_input("Contract step", min_value=1.0, value=1.0, step=1.0, key="v2_sim_contract_step")
    use_live_equity = st.checkbox("Use live equity as starting capital (Recommended)", value=True, key="v2_sim_use_live_equity")

    f1, f2, f3, f4 = st.columns(4)
    hedge_loss_usdt = f1.number_input("Hedge trigger (uPnL <=)", value=-8.0, step=1.0, key="v2_sim_hedge_loss_usdt")
    target_roi_pct = f2.number_input("Target ROI % per trim", min_value=1.0, value=200.0, step=10.0, key="v2_sim_target_roi_pct")
    limit_offset_pct = f3.number_input(
        "CLOSE LIMIT offset %",
        min_value=0.0,
        value=0.05,
        step=0.01,
        format="%.4f",
        key="v2_sim_limit_offset_pct",
    )
    open_limit_offset_pct = f4.number_input(
        "OPEN LIMIT offset %",
        min_value=0.0,
        value=0.05,
        step=0.01,
        format="%.4f",
        key="v2_sim_open_limit_offset_pct",
    )

    g1, g2, g3, g4 = st.columns(4)
    rebalance_trigger_pct = g1.number_input(
        "Rebalance trigger %",
        min_value=0.0,
        value=0.10,
        step=0.01,
        format="%.4f",
        key="v2_sim_rebalance_trigger_pct",
    )
    moderate_imbalance_ratio = g2.number_input(
        "Moderate imbalance ratio",
        min_value=1.0,
        value=1.5,
        step=0.1,
        key="v2_sim_moderate_ratio",
    )
    extreme_imbalance_ratio = g3.number_input(
        "Extreme imbalance ratio",
        min_value=1.0,
        value=3.0,
        step=0.1,
        key="v2_sim_extreme_ratio",
    )
    safe_mode_one_contract = g4.checkbox(
        "SAFE MODE: force 1 contract per action",
        value=True,
        key="v2_sim_safe_mode",
    )

    st.write("### Seed state")
    s1, s2, s3, s4 = st.columns(4)
    seed_long_qty = s1.number_input("Seed long qty", min_value=0.0, value=0.0, step=1.0, key="v2_sim_seed_long_qty")
    seed_short_qty = s2.number_input("Seed short qty", min_value=0.0, value=0.0, step=1.0, key="v2_sim_seed_short_qty")
    seed_long_entry = s3.number_input(
        "Seed long entry",
        min_value=0.0,
        value=0.0,
        step=0.0001,
        format="%.6f",
        key="v2_sim_seed_long_entry",
    )
    seed_short_entry = s4.number_input(
        "Seed short entry",
        min_value=0.0,
        value=0.0,
        step=0.0001,
        format="%.6f",
        key="v2_sim_seed_short_entry",
    )

    h1, h2, h3, h4 = st.columns(4)
    shock_step = h1.number_input("Shock step", min_value=0, max_value=5000, value=60, step=1, key="v2_sim_shock_step")
    shock_pct = h2.number_input(
        "Shock %",
        min_value=0.0,
        value=5.0,
        step=0.5,
        format="%.2f",
        key="v2_sim_shock_pct",
    )
    fatal_drawdown_pct = h3.number_input(
        "Fatal drawdown %",
        min_value=1.0,
        value=50.0,
        step=1.0,
        format="%.2f",
        key="v2_sim_fatal_drawdown_pct",
    )
    fatal_margin_ratio_pct = h4.number_input(
        "Fatal margin ratio %",
        min_value=1.0,
        value=80.0,
        step=1.0,
        format="%.2f",
        key="v2_sim_fatal_margin_ratio_pct",
    )

    i1, i2, i3, i4 = st.columns(4)
    fatal_gross_multiplier = i1.number_input(
        "Fatal gross multiplier",
        min_value=1.0,
        value=8.0,
        step=0.5,
        format="%.2f",
        key="v2_sim_fatal_gross_multiplier",
    )
    warning_drawdown_pct = i2.number_input(
        "Warning drawdown %",
        min_value=0.1,
        value=10.0,
        step=0.5,
        format="%.2f",
        key="v2_sim_warning_drawdown_pct",
    )
    warning_margin_ratio_pct = i3.number_input(
        "Warning margin ratio %",
        min_value=0.1,
        value=25.0,
        step=0.5,
        format="%.2f",
        key="v2_sim_warning_margin_ratio_pct",
    )
    warning_gross_multiplier = i4.number_input(
        "Warning gross multiplier",
        min_value=1.0,
        value=4.0,
        step=0.5,
        format="%.2f",
        key="v2_sim_warning_gross_multiplier",
    )

    k1, k2, k3 = st.columns(3)
    warning_actions_count = k1.number_input(
        "Warning actions count",
        min_value=1,
        value=50,
        step=5,
        key="v2_sim_warning_actions_count",
    )
    warning_imbalance_ratio = k2.number_input(
        "Warning imbalance ratio",
        min_value=1.0,
        value=8.0,
        step=0.5,
        format="%.2f",
        key="v2_sim_warning_imbalance_ratio",
    )
    gross_cap_contracts = k3.number_input(
        "Gross cap contracts (0=off)",
        min_value=0.0,
        value=20.0,
        step=1.0,
        format="%.2f",
        key="v2_sim_gross_cap_contracts",
    )

    st.write("### Live market forecast")
    live_seed = None
    try:
        live_seed = build_live_market_seed(con, symbol=symbol)
        _render_live_seed(live_seed)
    except Exception as exc:
        st.info(f"Live seed unavailable: {exc}")

    l1, l2 = st.columns(2)
    run_pack_button = l1.button("Run 3-scenario forecast", key="v2_sim_run_pack")
    run_button = l2.button("Run simulation", key="v2_sim_run")

    if run_pack_button:
        try:
            pack = simulate_market_scenario_pack(
                con,
                symbol=symbol,
                initial_side=initial_side,
                steps=int(steps),
                starting_capital=float(starting_capital),
                secured_capital=float(secured_capital),
                leverage=float(leverage),
                hedge_loss_usdt=float(hedge_loss_usdt),
                target_roi_pct=float(target_roi_pct),
                limit_offset_pct=float(limit_offset_pct),
                open_limit_offset_pct=float(open_limit_offset_pct),
                rebalance_trigger_pct=float(rebalance_trigger_pct),
                moderate_imbalance_ratio=float(moderate_imbalance_ratio),
                extreme_imbalance_ratio=float(extreme_imbalance_ratio),
                safe_mode_one_contract=bool(safe_mode_one_contract),
                contract_step=float(contract_step),
                drift_pct_per_step=float(drift_pct_per_step),
                oscillation_pct=float(oscillation_pct),
                fatal_drawdown_pct=float(fatal_drawdown_pct),
                fatal_margin_ratio_pct=float(fatal_margin_ratio_pct),
                fatal_gross_multiplier=float(fatal_gross_multiplier),
                warning_drawdown_pct=float(warning_drawdown_pct),
                warning_margin_ratio_pct=float(warning_margin_ratio_pct),
                warning_gross_multiplier=float(warning_gross_multiplier),
                warning_actions_count=int(warning_actions_count),
                warning_imbalance_ratio=float(warning_imbalance_ratio),
                gross_cap_contracts=float(gross_cap_contracts),
                use_live_equity=bool(use_live_equity),
            )
            _render_scenario_pack(pack)
            with st.expander("Scenario pack seed JSON", expanded=False):
                st.json(pack.get("seed") or {})
        except Exception as exc:
            st.error(f"3-scenario forecast failed: {exc}")

    if not run_button:
        st.info("Set parameters and click 'Run simulation'.")
        return

    result = simulate_strategy_market(
        symbol=symbol,
        initial_side=initial_side,
        start_price=float(start_price),
        steps=int(steps),
        scenario=scenario,
        starting_capital=float(starting_capital),
        secured_capital=float(secured_capital),
        leverage=float(leverage),
        hedge_loss_usdt=float(hedge_loss_usdt),
        target_roi_pct=float(target_roi_pct),
        limit_offset_pct=float(limit_offset_pct),
        open_limit_offset_pct=float(open_limit_offset_pct),
        rebalance_trigger_pct=float(rebalance_trigger_pct),
        moderate_imbalance_ratio=float(moderate_imbalance_ratio),
        extreme_imbalance_ratio=float(extreme_imbalance_ratio),
        safe_mode_one_contract=bool(safe_mode_one_contract),
        contract_step=float(contract_step),
        drift_pct_per_step=float(drift_pct_per_step),
        oscillation_pct=float(oscillation_pct),
        shock_step=int(shock_step),
        shock_pct=float(shock_pct),
        fatal_drawdown_pct=float(fatal_drawdown_pct),
        fatal_margin_ratio_pct=float(fatal_margin_ratio_pct),
        fatal_gross_multiplier=float(fatal_gross_multiplier),
        seed_long_qty=float(seed_long_qty),
        seed_short_qty=float(seed_short_qty),
        seed_long_entry=float(seed_long_entry),
        seed_short_entry=float(seed_short_entry),
        warning_drawdown_pct=float(warning_drawdown_pct),
        warning_margin_ratio_pct=float(warning_margin_ratio_pct),
        warning_gross_multiplier=float(warning_gross_multiplier),
        warning_actions_count=int(warning_actions_count),
        warning_imbalance_ratio=float(warning_imbalance_ratio),
        gross_cap_contracts=float(gross_cap_contracts),
    )

    summary = result["summary"]
    trace_df = result["trace_df"]
    actions_df = result["actions_df"]
    warning_df = result["warning_df"]
    fatal_df = result["fatal_df"]

    _render_summary(summary)

    if not warning_df.empty:
        st.warning(
            f"Warning moments detected: {len(warning_df)}. "
            f"First warning step={summary.get('first_warning_step')} | "
            f"reason={summary.get('first_warning_reason')}"
        )
    else:
        st.success("No warning moments detected for the current simulation settings.")

    if not fatal_df.empty:
        st.error(
            f"Fatal moments detected: {len(fatal_df)}. "
            f"First fatal step={summary.get('first_fatal_step')} | "
            f"reason={summary.get('first_fatal_reason')}"
        )
    else:
        st.info("No fatal moments detected for the current simulation settings.")

    _render_dataframe_block("Warning moments", warning_df)
    _render_dataframe_block("Fatal moments", fatal_df)
    _render_dataframe_block("Action log", actions_df, "strategy_log")

    st.write("### Trace controls")
    j1, j2, j3 = st.columns(3)
    only_warning = j1.checkbox("Show only warning rows", value=False, key="v2_sim_only_warning")
    only_fatal = j2.checkbox("Show only fatal rows", value=False, key="v2_sim_only_fatal")
    trace_limit = j3.number_input("Trace rows to show", min_value=20, max_value=5000, value=200, step=20, key="v2_sim_trace_limit")

    display_trace_df = trace_df
    if only_warning and not trace_df.empty and "warning" in trace_df.columns:
        display_trace_df = display_trace_df[display_trace_df["warning"] == True]  # noqa: E712
    if only_fatal and not display_trace_df.empty and "fatal" in display_trace_df.columns:
        display_trace_df = display_trace_df[display_trace_df["fatal"] == True]  # noqa: E712

    display_trace_df = display_trace_df.head(int(trace_limit))
    _render_dataframe_block("Trace", display_trace_df)

    with st.expander("Simulation summary JSON", expanded=False):
        st.json(summary)

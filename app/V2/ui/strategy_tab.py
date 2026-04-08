from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from app.V2.ui.strategy_components import (
    render_decision_box,
    render_guards_summary,
    render_legs_summary,
    render_lot_summary,
    render_sizing_summary,
    render_state_banner,
    render_targets_summary,
    render_top_summary,
)
from core.streamlit_services.common import now_utc_iso
from core.streamlit_services.strategy_engine import evaluate_strategy_state
from core.streamlit_services.strategy_service import (
    enqueue_armed_close_limit,
    enqueue_armed_open_limit,
)
from core.streamlit_services.symbol_picker_service import (
    load_futures_symbol_options,
    resolve_default_symbol_index,
)
from core.streamlit_services.table_columns_service import project_df_columns


def _render_strategy_log() -> None:
    if st.session_state.get("v2_strategy_log"):
        st.write("### Strategy log (last 50)")
        df = pd.DataFrame(st.session_state.v2_strategy_log)
        st.dataframe(
            project_df_columns(df, "strategy_log"),
            width="stretch",
            hide_index=True,
        )


def _ensure_strategy_session_state() -> None:
    if "v2_strategy_last_action_ts" not in st.session_state:
        st.session_state.v2_strategy_last_action_ts = 0.0

    if "v2_strategy_log" not in st.session_state:
        st.session_state.v2_strategy_log = []


def _append_strategy_log(
    *,
    symbol: str,
    result: dict,
    decision,
    dry: bool,
    safe_mode_one_contract: bool,
) -> None:
    st.session_state.v2_strategy_log.insert(
        0,
        {
            "ts": now_utc_iso(),
            "symbol": symbol,
            "regime": result["regime_params"]["regime"],
            "strategy_state": result["strategy_state"],
            "action_reason": result["action_reason"],
            "no_action_reason": result["no_action_reason"],
            "decision": str(decision),
            "dry": bool(dry),
            "LONG": result["L"],
            "SHORT": result["S"],
            "last_price": result["current_last_price"],
            "active_strategy_capital": result["active_strategy_capital"],
            "imbalance_ratio": result["imbalance_ratio"],
            "safe_mode_one_contract": bool(safe_mode_one_contract),
            "lot_trim_ready": result["lot_trim_ready"],
            "leg_trim_ready": result["leg_trim_ready"],
            "open_class": result["open_class"],
            "close_class": result["close_class"],
            "gross_contracts": (result.get("gross_block") or {}).get("gross_contracts"),
            "gross_cap_contracts": (result.get("gross_block") or {}).get("gross_cap_contracts"),
            "gross_cap_blocked": (result.get("gross_block") or {}).get("gross_cap_blocked"),
        },
    )
    st.session_state.v2_strategy_log = st.session_state.v2_strategy_log[:50]


def _handle_strategy_decision(
    *,
    con,
    symbol: str,
    priority: int,
    leverage: float,
    dry: bool,
    decision,
) -> bool:
    if not decision:
        return False

    decision_kind = decision[0]

    if decision_kind == "OPEN_LIMIT":
        _, side, qty, limit_price, note = decision

        if dry:
            st.warning(
                f"DRY-RUN: would enqueue ARMED OPEN+LIMIT qty={qty:.0f} @ {limit_price:.6f} "
                f"with leverage={int(float(leverage))}."
            )
            return True

        try:
            enqueue_armed_open_limit(
                con,
                symbol=symbol,
                side=side,
                qty=float(qty),
                limit_price=float(limit_price),
                leverage=float(leverage),
                note=note,
                priority=int(priority),
            )
            st.session_state.v2_strategy_last_action_ts = time.time()
            st.success(
                f"Enqueued ARMED action (OPEN+LIMIT) qty={qty:.0f} @ {limit_price:.6f} "
                f"with leverage={int(float(leverage))}."
            )
            return True
        except Exception as exc:
            st.error(f"Enqueue failed: {exc}")
            return False

    if decision_kind == "CLOSE_LIMIT":
        _, side, qty, limit_price, note = decision

        if dry:
            st.warning(
                f"DRY-RUN: would enqueue ARMED CLOSE+LIMIT qty={qty:.0f} @ {limit_price:.6f} "
                f"with leverage={int(float(leverage))}."
            )
            return True

        try:
            enqueue_armed_close_limit(
                con,
                symbol=symbol,
                side=side,
                qty=float(qty),
                limit_price=float(limit_price),
                leverage=float(leverage),
                note=note,
                priority=int(priority),
            )
            st.session_state.v2_strategy_last_action_ts = time.time()
            st.success(
                f"Enqueued ARMED action (CLOSE+LIMIT) qty={qty:.0f} @ {limit_price:.6f} "
                f"with leverage={int(float(leverage))}."
            )
            return True
        except Exception as exc:
            st.error(f"Enqueue failed: {exc}")
            return False

    return False


def _render_eligible_lots(result: dict) -> None:
    eligible_df = result.get("eligible_lots_df")
    if eligible_df is None or eligible_df.empty:
        return

    st.write("### Eligible open lots now")
    eligible_cols = [
        "id",
        "symbol",
        "side",
        "qty_remaining",
        "entry_price",
        "target_price",
        "opened_at",
        "source_task_type",
        "source_task_id",
        "status",
    ]
    eligible_cols = [col for col in eligible_cols if col in eligible_df.columns]
    st.dataframe(eligible_df[eligible_cols], width="stretch", hide_index=True)


def _render_debug_json(result: dict) -> None:
    with st.expander("Debug JSON", expanded=False):
        st.write("Snapshot")
        st.json(result["snapshot"])

        st.write("Symbol debug")
        st.json(result.get("symbol_debug", {}))

        st.write("Guards")
        st.json(result["guards"])

        st.write("Sizing")
        st.json(result["sizing"])

        st.write("Derived targets")
        st.json(result["derived_targets"])

        st.write("Imbalance")
        st.json(result["imbalance_block"])

        st.write("Gross block")
        st.json(result.get("gross_block", {}))

        st.write("Lot debug")
        st.json(result["lot_debug"])


def render_strategy_tab(con) -> None:
    st.subheader("Strategy (AUTO) — LIMIT only")
    st.caption(
        "SAFE MODE keeps every action at 1 contract until the full logic is validated. "
        "Both OPEN and CLOSE use LIMIT only."
    )

    try:
        from streamlit_autorefresh import st_autorefresh  # type: ignore
        autorefresh_available = True
    except Exception:
        autorefresh_available = False

    symbol_options = load_futures_symbol_options(con)

    c1, c2, c3 = st.columns(3)
    if symbol_options:
        symbol = c1.selectbox(
            "Symbol (strategy)",
            options=symbol_options,
            index=resolve_default_symbol_index(symbol_options, "ADA/USDT:USDT"),
            key="v2_strategy_symbol",
        )
    else:
        symbol = c1.text_input("Symbol (strategy)", value="ADA/USDT:USDT", key="v2_strategy_symbol_fallback").strip()

    initial_side = c2.selectbox("Initial side", ["LONG", "SHORT"], index=1, key="v2_initial_side")
    priority = c3.number_input("Priority (lower runs first)", min_value=0, value=50, step=1, key="v2_priority")

    d1, d2, d3, d4 = st.columns(4)
    starting_capital = d1.number_input("Starting capital", min_value=1.0, value=100.0, step=10.0, key="v2_starting_capital")
    secured_capital = d2.number_input("Secured capital", min_value=0.0, value=0.0, step=10.0, key="v2_secured_capital")
    leverage = d3.number_input("Leverage for sizing / ROI math", min_value=1.0, value=300.0, step=1.0, key="v2_leverage")
    dry = d4.checkbox("Dry-run (no enqueue)", value=False, key="v2_dry")

    e1, e2, e3, e4 = st.columns(4)
    hedge_loss_usdt = e1.number_input("Hedge trigger (uPnL <=)", value=-8.0, step=1.0, key="v2_hedge_loss_usdt")
    target_roi_pct = e2.number_input("Target ROI % per trim", min_value=1.0, value=200.0, step=10.0, key="v2_target_roi_pct")
    limit_offset_pct = e3.number_input(
        "CLOSE LIMIT offset %",
        min_value=0.0,
        value=0.05,
        step=0.01,
        format="%.4f",
        key="v2_limit_offset_pct",
    )
    open_limit_offset_pct = e4.number_input(
        "OPEN LIMIT offset %",
        min_value=0.0,
        value=0.05,
        step=0.01,
        format="%.4f",
        key="v2_open_limit_offset_pct",
    )

    f1, f2, f3, f4 = st.columns(4)
    rebalance_trigger_pct = f1.number_input(
        "Rebalance trigger %",
        min_value=0.0,
        value=0.10,
        step=0.01,
        format="%.4f",
        key="v2_rebalance_trigger_pct",
    )
    moderate_imbalance_ratio = f2.number_input(
        "Moderate imbalance ratio",
        min_value=1.0,
        value=1.5,
        step=0.1,
        key="v2_moderate_ratio",
    )
    extreme_imbalance_ratio = f3.number_input(
        "Extreme imbalance ratio",
        min_value=1.0,
        value=3.0,
        step=0.1,
        key="v2_extreme_ratio",
    )
    auto = f4.checkbox("Auto-run", value=False, key="v2_auto")

    g1, g2, g3 = st.columns(3)
    safe_mode_one_contract = g1.checkbox(
        "SAFE MODE: force 1 contract per action",
        value=True,
        key="v2_safe_mode",
    )
    contract_step = g2.number_input("Contract step", min_value=1.0, value=1.0, step=1.0, key="v2_contract_step")
    gross_cap_contracts = g3.number_input(
        "Gross cap (contracts, 0=off)",
        min_value=0.0,
        value=20.0,
        step=1.0,
        key="v2_gross_cap_contracts",
    )

    if autorefresh_available:
        r1, r2 = st.columns([1, 3])
        refresh_ms = r1.selectbox("Refresh interval", [2000, 3000, 5000, 8000], index=1, key="v2_refresh_ms")
        r2.caption("Auto-run uses autorefresh. If missing: pip install streamlit-autorefresh")
        if auto:
            st_autorefresh(interval=int(refresh_ms), key="v2_strategy_autorefresh")
    else:
        st.warning(
            "Auto-refresh not installed. For Auto-run: pip install streamlit-autorefresh. "
            "You can still use 'Run once'."
        )

    run_once = st.button("Run once (evaluate now)", key="v2_run_once")

    _ensure_strategy_session_state()

    do_eval = run_once or auto
    if not do_eval:
        _render_strategy_log()
        return

    gross_cap_value = float(gross_cap_contracts)
    gross_cap_for_engine = gross_cap_value if gross_cap_value > 0 else None

    result = evaluate_strategy_state(
        con=con,
        symbol=symbol,
        initial_side=initial_side,
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
        cooldown_sec_override=None,
        last_action_ts=float(st.session_state.v2_strategy_last_action_ts),
        gross_cap_contracts=gross_cap_for_engine,
    )

    render_state_banner(
        result.get("strategy_state"),
        result.get("action_reason"),
        result.get("no_action_reason"),
    )
    render_top_summary(result)

    gross_block = result.get("gross_block") or {}
    if gross_block.get("gross_cap_enabled"):
        st.caption(
            f"Gross exposure: {float(gross_block.get('gross_contracts') or 0.0):.4f} / "
            f"{float(gross_block.get('gross_cap_contracts') or 0.0):.4f} contracts"
        )

    left, right = st.columns(2)
    with left:
        render_legs_summary(result)
        render_targets_summary(result)

    with right:
        render_sizing_summary(result)
        render_lot_summary(result)

    render_guards_summary(result)
    render_decision_box(result)
    _render_eligible_lots(result)

    decision = result["decision"]
    handled = _handle_strategy_decision(
        con=con,
        symbol=symbol,
        priority=int(priority),
        leverage=float(leverage),
        dry=bool(dry),
        decision=decision,
    )

    if decision and handled:
        _append_strategy_log(
            symbol=symbol,
            result=result,
            decision=decision,
            dry=bool(dry),
            safe_mode_one_contract=bool(safe_mode_one_contract),
        )
    elif result.get("strategy_state") == "BLOCKED":
        _append_strategy_log(
            symbol=symbol,
            result=result,
            decision=decision,
            dry=bool(dry),
            safe_mode_one_contract=bool(safe_mode_one_contract),
        )

    _render_debug_json(result)
    _render_strategy_log()
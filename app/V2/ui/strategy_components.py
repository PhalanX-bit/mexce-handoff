from __future__ import annotations

import math
from typing import Any

import streamlit as st


def fmt_num(value: Any, digits: int = 4) -> str:
    try:
        if value is None:
            return "—"
        if isinstance(value, float) and math.isinf(value):
            return "∞"
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "—"


def fmt_price(value: Any) -> str:
    return fmt_num(value, 6)


def fmt_contracts(value: Any) -> str:
    return fmt_num(value, 2)


def fmt_pct(value: Any) -> str:
    try:
        if value is None:
            return "—"
        if isinstance(value, float) and math.isinf(value):
            return "∞"
        return f"{float(value):.3f}%"
    except Exception:
        return "—"


def _resolve_reason(action_reason: str, no_action_reason: str) -> str:
    return action_reason or no_action_reason or "—"


def render_state_banner(strategy_state: str, action_reason: str, no_action_reason: str) -> None:
    state = str(strategy_state or "UNDEFINED").upper()
    reason = _resolve_reason(action_reason, no_action_reason)
    message = f"State: {strategy_state} | reason: {reason}"

    if state.startswith("BLOCKED"):
        st.warning(message)
    elif state.startswith("COOLDOWN"):
        st.info(message)
    elif state.startswith("HEDGED"):
        st.success(message)
    elif state in ("NO_POSITION", "ONE_SIDED_LONG", "ONE_SIDED_SHORT"):
        st.info(message)
    else:
        st.write(message)


def render_top_summary(result: dict) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Strategy state", str(result.get("strategy_state") or "—"))
    c2.metric("Regime", str((result.get("regime_params") or {}).get("regime") or "—"))
    c3.metric("Last price", fmt_price(result.get("current_last_price")))
    c4.metric(
        "Imbalance ratio",
        "∞" if result.get("imbalance_ratio") == float("inf") else fmt_num(result.get("imbalance_ratio"), 3),
    )
    c5.metric("Active strategy capital", fmt_num(result.get("active_strategy_capital"), 4))


def render_legs_summary(result: dict) -> None:
    long_leg = result.get("L") or {}
    short_leg = result.get("S") or {}

    st.write("### Legs summary")
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**LONG**")
        st.write(f"Contracts: {fmt_contracts(long_leg.get('contracts'))}")
        st.write(f"Entry: {fmt_price(long_leg.get('entry_price'))}")
        st.write(f"uPnL: {fmt_num(long_leg.get('unrealized_pnl'), 4)}")

    with c2:
        st.markdown("**SHORT**")
        st.write(f"Contracts: {fmt_contracts(short_leg.get('contracts'))}")
        st.write(f"Entry: {fmt_price(short_leg.get('entry_price'))}")
        st.write(f"uPnL: {fmt_num(short_leg.get('unrealized_pnl'), 4)}")


def render_targets_summary(result: dict) -> None:
    derived_targets = result.get("derived_targets") or {}

    st.write("### Targets")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Target ROI %", fmt_num(derived_targets.get("target_roi_pct"), 2))
    c2.metric(
        "Target move %",
        fmt_pct(
            (derived_targets.get("target_move_pct") or 0.0) * 100.0
            if derived_targets.get("target_move_pct") is not None
            else None
        ),
    )
    c3.metric("Long target", fmt_price(derived_targets.get("long_target_price")))
    c4.metric("Short target", fmt_price(derived_targets.get("short_target_price")))


def render_sizing_summary(result: dict) -> None:
    sizing = result.get("sizing") or {}

    st.write("### Sizing")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Envelope margin", fmt_num(sizing.get("symbol_envelope_margin"), 4))
    c2.metric("Used margin", fmt_num(sizing.get("used_symbol_margin"), 4))
    c3.metric("Initial contracts", fmt_contracts(sizing.get("final_initial_contracts")))
    c4.metric("Rebalance contracts", fmt_contracts(sizing.get("final_rebalance_contracts")))


def render_guards_summary(result: dict) -> None:
    guards = result.get("guards") or {}

    st.write("### Guards")
    c1, c2, c3 = st.columns(3)
    c1.metric("Active queue task", "YES" if guards.get("active_queue_task") else "NO")
    c2.metric("Active pending LIMIT", "YES" if guards.get("active_pending_limit") else "NO")
    c3.metric("Active pending CHASE", "YES" if guards.get("active_pending_chase") else "NO")


def render_lot_summary(result: dict) -> None:
    lot_debug = result.get("lot_debug") or {}

    st.write("### Lots")
    c1, c2, c3 = st.columns(3)
    c1.metric("Open lots", int(lot_debug.get("open_lots_count") or 0))
    c2.metric("Eligible lots", int(lot_debug.get("eligible_open_lots_count") or 0))
    c3.metric("Eligible qty", fmt_contracts(lot_debug.get("eligible_open_qty")))


def render_decision_box(result: dict) -> None:
    decision = result.get("decision")
    action_reason = result.get("action_reason")
    no_action_reason = result.get("no_action_reason")
    open_class = result.get("open_class")
    close_class = result.get("close_class")

    st.write("### Decision")

    if not decision:
        st.info(
            f"No action. "
            f"open_class={open_class or '—'} | close_class={close_class or '—'} | "
            f"reason={_resolve_reason(action_reason, no_action_reason)}"
        )
        return

    kind, side, qty, price, note = decision
    st.success(f"{kind} | side={side} | qty={fmt_contracts(qty)} | price={fmt_price(price)}")

    if note:
        st.caption(note)

    st.caption(f"open_class={open_class or '—'} | close_class={close_class or '—'}")
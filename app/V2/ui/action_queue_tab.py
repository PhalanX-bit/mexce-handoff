from __future__ import annotations

from decimal import Decimal, InvalidOperation
import pandas as pd
import streamlit as st

from core.mexc_direct import get_contract_meta
from core.streamlit_services.common import now_utc_iso
from core.streamlit_services.queue_service import (
    delete_action_queue_by_statuses,
    delete_action_queue_row,
    get_action_queue_status_counts,
    queue_arm,
    queue_cancel,
    queue_insert_v2,
    queue_list_v2,
)
from core.streamlit_services.symbol_picker_service import (
    load_futures_symbol_options,
    resolve_default_symbol_index,
)
from core.streamlit_services.table_columns_service import project_df_columns


def _safe_contract_meta(symbol: str) -> dict | None:
    try:
        return get_contract_meta(symbol)
    except Exception:
        return None


def _safe_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _is_valid_step_value(value: float, step: float) -> bool:
    d_value = _safe_decimal(value)
    d_step = _safe_decimal(step)

    if d_value is None or d_step is None or d_step <= 0:
        return True

    units = d_value / d_step
    return units == units.to_integral_value()


def render_action_queue_tab(con) -> None:
    st.subheader("Action Queue (PENDING → ARM → executor handles ARMED)")
    st.caption("Manual queue creation and strategy both use LIMIT only. CHASE stays visible only for legacy history.")

    qcounts = get_action_queue_status_counts(con)

    q1, q2, q3, q4, q5, q6 = st.columns(6)
    q1.metric("PENDING", qcounts.get("PENDING", 0))
    q2.metric("ARMED", qcounts.get("ARMED", 0))
    q3.metric("RUNNING", qcounts.get("RUNNING", 0))
    q4.metric("DONE", qcounts.get("DONE", 0))
    q5.metric("FAILED", qcounts.get("FAILED", 0))
    q6.metric("CANCELED", qcounts.get("CANCELED", 0))

    symbol_options = load_futures_symbol_options(con)
    default_index = resolve_default_symbol_index(symbol_options, "ADA/USDT:USDT")

    with st.form("v2_queue_create", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)

        if symbol_options:
            symbol = c1.selectbox(
                "Symbol",
                options=symbol_options,
                index=default_index,
                key="v2_queue_symbol_select",
            )
        else:
            symbol = c1.text_input("Symbol", value="ADA/USDT:USDT", key="v2_queue_symbol_fallback").strip()

        panel_mode = c2.selectbox("Panel mode", ["CLOSE", "OPEN"], index=0)
        side = c3.selectbox("Side", ["SHORT", "LONG"], index=0)
        qty = c4.number_input("Qty (Cont)", min_value=0.0, value=50.0, step=1.0)

        symbol = str(symbol or "").strip()
        contract_meta = _safe_contract_meta(symbol) if symbol else None
        max_leverage = None if not contract_meta else contract_meta.get("max_leverage")
        price_tick = None if not contract_meta else contract_meta.get("price_tick")
        qty_step = None if not contract_meta else contract_meta.get("qty_step")
        min_qty = None if not contract_meta else contract_meta.get("min_qty")

        c5, c6, c7, c8 = st.columns(4)
        order_kind = c5.selectbox("Order kind", ["LIMIT", "POST_ONLY", "MARKET"], index=0)

        price_step_for_input = float(price_tick) if isinstance(price_tick, (int, float)) and float(price_tick) > 0 else 0.0001
        limit_price = c6.number_input(
            "Limit price (USDT) (0 = none)",
            min_value=0.0,
            value=0.0,
            step=price_step_for_input,
            format="%.6f",
        )
        reduce_only = c7.checkbox("Reduce-only", value=True)

        leverage_input_max = int(max_leverage) if isinstance(max_leverage, int) and max_leverage > 0 else 500
        leverage_default = min(100, leverage_input_max)

        leverage = c8.number_input(
            "Leverage",
            min_value=1,
            max_value=leverage_input_max,
            value=leverage_default,
            step=1,
        )

        if max_leverage is not None:
            c8.caption(f"Max leverage for {symbol}: {int(max_leverage)}x")
        else:
            c8.caption("Max leverage unknown; using conservative default 100x and allowing up to 500x.")

        c9, c10, c11, c12 = st.columns(4)
        trigger_type = c9.selectbox("Trigger type", ["manual", "immediate", "price"], index=0)
        trigger_op = c10.selectbox("Trigger op", ["<=", ">="], index=0)
        trigger_price = c11.number_input(
            "Trigger price (0 = none)",
            min_value=0.0,
            value=0.0,
            step=0.0001,
            format="%.6f",
        )
        priority = c12.number_input("Priority (lower runs first)", min_value=0, value=100, step=1)

        if contract_meta:
            st.caption(
                f"Contract meta → price_tick={price_tick or '—'} | qty_step={qty_step or '—'} | "
                f"min_qty={min_qty or '—'} | max_leverage={max_leverage or '—'}"
            )

        note = st.text_input("Note (optional)", value="")
        submit = st.form_submit_button("Create PENDING")

        if submit:
            symbol = str(symbol or "").strip()

            if not symbol:
                st.error("Symbol is required.")
                st.stop()

            qty = float(qty)
            if qty <= 0:
                st.error("Qty must be > 0.")
                st.stop()

            if isinstance(min_qty, (int, float)) and float(min_qty) > 0 and qty < float(min_qty):
                st.error(f"Qty {qty} is below min_qty for {symbol}: {min_qty}.")
                st.stop()

            if isinstance(qty_step, (int, float)) and float(qty_step) > 0:
                if not _is_valid_step_value(qty, float(qty_step)):
                    st.error(f"Qty {qty} is not a valid multiple of qty_step {qty_step} for {symbol}.")
                    st.stop()

            if order_kind in ("LIMIT", "POST_ONLY"):
                if float(limit_price) <= 0:
                    st.error(f"{order_kind} requires limit_price > 0.")
                    st.stop()

                if isinstance(price_tick, (int, float)) and float(price_tick) > 0:
                    if not _is_valid_step_value(float(limit_price), float(price_tick)):
                        st.error(
                            f"Limit price {limit_price} is not a valid multiple of price_tick {price_tick} for {symbol}."
                        )
                        st.stop()

            if trigger_type == "price" and trigger_price <= 0:
                st.error("If trigger_type=price you must set trigger_price > 0.")
                st.stop()

            leverage = int(leverage)
            if leverage <= 0:
                st.error("Leverage is required.")
                st.stop()

            if isinstance(max_leverage, int) and max_leverage > 0 and leverage > max_leverage:
                st.error(f"Leverage {leverage} exceeds max allowed for {symbol}: {max_leverage}.")
                st.stop()

            payload = {
                "created_at": now_utc_iso(),
                "created_by": "streamlit_v2",
                "exchange": "mexc",
                "market_type": "swap",
                "symbol": symbol,
                "intent": "close" if panel_mode == "CLOSE" else "open",
                "side": side,
                "reduce_only": 1 if reduce_only else 0,
                "qty": qty,
                "qty_unit": "contracts",
                "order_type": "limit" if order_kind in ("POST_ONLY", "LIMIT") else "market",
                "limit_price": float(limit_price) if limit_price > 0 else None,
                "trigger_type": trigger_type,
                "trigger_op": trigger_op if trigger_type == "price" else None,
                "trigger_price": float(trigger_price) if trigger_type == "price" else None,
                "status": "PENDING",
                "priority": int(priority),
                "note": note.strip() if note.strip() else None,
                "panel_mode": panel_mode,
                "order_kind": order_kind,
                "leverage": leverage,
                "last_update_at": now_utc_iso(),
            }

            inserted = queue_insert_v2(con, payload)
            con.commit()
            if inserted == 1:
                st.success("Created PENDING action.")
            else:
                st.error("Insert failed (unexpected). Check logs.")

    st.divider()

    f1, f2, f3 = st.columns([1, 1, 1])
    status_filter = f1.selectbox(
        "Status filter",
        ["ALL", "PENDING", "ARMED", "RUNNING", "DONE", "FAILED", "CANCELED"],
        index=0,
        key="v2_queue_status_filter",
    )
    limit = f2.number_input("Rows", min_value=20, value=200, step=20, key="v2_queue_limit")
    if f3.button("Refresh table", key="v2_queue_refresh"):
        st.rerun()

    rows = queue_list_v2(con, status_filter, int(limit))
    if not rows:
        st.info("No actions yet.")
        return

    dfq = pd.DataFrame(rows)
    dfq = project_df_columns(dfq, "action_queue_main")
    st.dataframe(dfq, width="stretch", hide_index=True)

    st.write("### Controls")
    cA, cB, cC = st.columns(3)
    action_id = cA.number_input("Action ID", min_value=1, value=int(pd.DataFrame(rows)["id"].iloc[0]), step=1, key="v2_action_id")

    if cB.button("ARM selected (PENDING→ARMED)", key="v2_arm_selected"):
        queue_arm(con, int(action_id))
        con.commit()
        st.success(f"ARMED {action_id}")
        st.rerun()

    if cC.button("CANCEL selected (PENDING/ARMED only)", key="v2_cancel_selected"):
        queue_cancel(con, int(action_id))
        con.commit()
        st.success(f"CANCELED {action_id}")
        st.rerun()

    cD, cE, cF = st.columns(3)

    if cD.button("Delete selected row", key="v2_delete_selected"):
        n = delete_action_queue_row(con, int(action_id))
        if n:
            st.success(f"Deleted action_queue row id={int(action_id)}")
        else:
            st.warning("No row deleted.")
        st.rerun()

    if cE.button("Delete DONE/FAILED/CANCELED", key="v2_delete_done_failed_canceled"):
        n = delete_action_queue_by_statuses(con, ["DONE", "FAILED", "CANCELED"])
        st.success(f"Deleted rows: {n}")
        st.rerun()

    if cF.button("Delete DONE only", key="v2_delete_done_only"):
        n = delete_action_queue_by_statuses(con, ["DONE"])
        st.success(f"Deleted DONE rows: {n}")
        st.rerun()

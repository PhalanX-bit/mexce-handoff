from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.common import _to_float_series
from core.streamlit_services.dashboard_service import get_latest_ticker_price_for_symbol
from core.streamlit_services.lots_service import (
    compute_eligible_lots_df,
    get_distinct_lot_symbols,
    list_lot_realizations,
    list_position_lots,
)


def render_lots_tab(con) -> None:
    st.subheader("Lots")
    st.caption("View open lots, realized lots, and which lots are eligible for trim at the current market price.")

    lot_symbols = get_distinct_lot_symbols(con)
    lot_symbol_filter = st.selectbox(
        "Symbol filter",
        ["ALL"] + lot_symbols if lot_symbols else ["ALL"],
        index=0,
        key="v2_lots_symbol_filter",
    )

    lot_status_filter = st.selectbox(
        "Open lots status",
        ["ALL", "OPEN", "CLOSED"],
        index=0,
        key="v2_lots_status_filter",
    )

    lots_limit = st.number_input("Rows per table", min_value=20, value=200, step=20, key="v2_lots_rows_limit")

    if st.button("Refresh lots", key="v2_refresh_lots"):
        st.rerun()

    lots_rows = list_position_lots(con, symbol=lot_symbol_filter, status=lot_status_filter, limit=int(lots_limit))
    realizations_rows = list_lot_realizations(con, symbol=lot_symbol_filter, limit=int(lots_limit))

    df_lots = pd.DataFrame(lots_rows) if lots_rows else pd.DataFrame()
    df_real = pd.DataFrame(realizations_rows) if realizations_rows else pd.DataFrame()

    current_lot_price = None
    if lot_symbol_filter != "ALL":
        current_lot_price = get_latest_ticker_price_for_symbol(con, lot_symbol_filter)

    top1, top2, top3 = st.columns(3)
    top1.metric(
        "Open lot rows",
        int(len(df_lots[df_lots["status"] == "OPEN"])) if not df_lots.empty and "status" in df_lots.columns else 0,
    )
    top2.metric("Realizations", int(len(df_real)) if not df_real.empty else 0)
    top3.metric("Current price", f"{current_lot_price:.6f}" if current_lot_price is not None else "—")

    if not df_lots.empty:
        for col in ["qty_opened", "qty_remaining", "entry_price", "target_roi_pct", "leverage", "target_price"]:
            _to_float_series(df_lots, col)

        st.write("### position_lots")
        lot_cols = [
            "id",
            "symbol",
            "side",
            "qty_opened",
            "qty_remaining",
            "entry_price",
            "target_price",
            "target_roi_pct",
            "leverage",
            "opened_at",
            "source_action_id",
            "source_task_type",
            "source_task_id",
            "status",
        ]
        lot_cols = [c for c in lot_cols if c in df_lots.columns]
        st.dataframe(df_lots[lot_cols], width="stretch", hide_index=True)

        if lot_symbol_filter != "ALL":
            eligible_df = compute_eligible_lots_df(df_lots, current_lot_price)
            st.write("### Eligible now")
            if not eligible_df.empty:
                eligible_qty = float(eligible_df["qty_remaining"].sum()) if "qty_remaining" in eligible_df.columns else 0.0
                st.caption(f"Eligible qty total: {eligible_qty:.4f}")
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
                eligible_cols = [c for c in eligible_cols if c in eligible_df.columns]
                st.dataframe(eligible_df[eligible_cols], width="stretch", hide_index=True)
            else:
                st.info("No eligible lots at the current price.")
        else:
            st.caption("Choose a specific symbol to see which open lots are eligible right now.")
    else:
        st.info("No rows in position_lots for the selected filter.")

    if not df_real.empty:
        for col in ["close_qty", "entry_price", "close_price", "target_price", "realized_roi_pct"]:
            _to_float_series(df_real, col)

        st.write("### lot_realizations")
        real_cols = [
            "id",
            "lot_id",
            "symbol",
            "side",
            "close_qty",
            "entry_price",
            "close_price",
            "target_price",
            "realized_roi_pct",
            "closed_at",
            "close_action_id",
            "close_task_type",
            "close_task_id",
            "note",
        ]
        real_cols = [c for c in real_cols if c in df_real.columns]
        st.dataframe(df_real[real_cols], width="stretch", hide_index=True)
    else:
        st.info("No rows in lot_realizations for the selected filter.")
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.common import _to_float_series
from core.streamlit_services.dashboard_service import get_latest_ticker_price_for_symbol
from core.streamlit_services.lots_service import (
    backfill_lot_from_action_queue,
    compute_eligible_lots_df,
    get_distinct_lot_symbols,
    list_done_actions_for_lot_backfill,
    list_lot_realizations,
    list_position_lots,
    preview_close_backfill_from_action_queue,
    summarize_open_lots_by_symbol,
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

    st.write("### Manual backfill from action_queue")
    st.caption("Use only when you know a DONE action was really filled but automatic fill detection could not confirm it.")

    done_actions = list_done_actions_for_lot_backfill(con, symbol=lot_symbol_filter, limit=100)
    done_action_ids = [int(r["id"]) for r in done_actions]

    b1, b2, b3, b4 = st.columns(4)
    selected_backfill_action_id = b1.selectbox(
        "DONE action id",
        done_action_ids if done_action_ids else [0],
        key="v2_lots_backfill_action_id",
    )
    backfill_qty_override = b2.number_input(
        "Fill qty override (0=use action qty)",
        min_value=0.0,
        value=0.0,
        step=1.0,
        key="v2_lots_backfill_qty",
    )
    backfill_price_override = b3.number_input(
        "Fill price override (0=use limit_price)",
        min_value=0.0,
        value=0.0,
        step=0.0001,
        format="%.6f",
        key="v2_lots_backfill_price",
    )
    backfill_eligible_first = b4.checkbox(
        "eligible_first for CLOSE",
        value=True,
        key="v2_lots_backfill_eligible_first",
    )

    selected_backfill_row = next(
        (row for row in done_actions if int(row["id"]) == int(selected_backfill_action_id)),
        None,
    )
    if selected_backfill_row:
        st.dataframe([selected_backfill_row], width="stretch", hide_index=True)
        if str(selected_backfill_row.get("panel_mode") or "").upper() == "CLOSE":
            try:
                preview = preview_close_backfill_from_action_queue(
                    con,
                    action_id=int(selected_backfill_action_id),
                    fill_qty=(None if float(backfill_qty_override) <= 0 else float(backfill_qty_override)),
                    fill_price=(None if float(backfill_price_override) <= 0 else float(backfill_price_override)),
                    eligible_first=bool(backfill_eligible_first),
                )
                st.write("### CLOSE backfill preview")
                st.caption(
                    f"Matched qty: {float(preview.get('matched_close_qty') or 0.0):.4f} / "
                    f"requested {float(preview.get('requested_close_qty') or 0.0):.4f} | "
                    f"unmatched: {float(preview.get('unmatched_close_qty') or 0.0):.4f}"
                )
                preview_rows = preview.get("preview_rows") or []
                if preview_rows:
                    st.dataframe(preview_rows, width="stretch", hide_index=True)
                else:
                    st.info("No matching open lots found for this CLOSE action.")
            except Exception as exc:
                st.warning(f"Preview failed: {exc}")

    if st.button("Backfill selected DONE action", key="v2_lots_backfill_action_button"):
        if not selected_backfill_row:
            st.warning("No DONE action selected.")
        else:
            try:
                result = backfill_lot_from_action_queue(
                    con,
                    action_id=int(selected_backfill_action_id),
                    fill_qty=(None if float(backfill_qty_override) <= 0 else float(backfill_qty_override)),
                    fill_price=(None if float(backfill_price_override) <= 0 else float(backfill_price_override)),
                    eligible_first=bool(backfill_eligible_first),
                )
                con.commit()
                st.success(f"Backfill completed for action_id={int(selected_backfill_action_id)}")
                st.json(result)
                st.rerun()
            except Exception as exc:
                st.error(f"Backfill failed: {exc}")

    lots_rows = list_position_lots(con, symbol=lot_symbol_filter, status=lot_status_filter, limit=int(lots_limit))
    realizations_rows = list_lot_realizations(con, symbol=lot_symbol_filter, limit=int(lots_limit))
    open_lot_summary_rows = summarize_open_lots_by_symbol(con, limit=100)

    df_lots = pd.DataFrame(lots_rows) if lots_rows else pd.DataFrame()
    df_real = pd.DataFrame(realizations_rows) if realizations_rows else pd.DataFrame()
    df_open_lot_summary = pd.DataFrame(open_lot_summary_rows) if open_lot_summary_rows else pd.DataFrame()

    current_lot_price = None
    if lot_symbol_filter != "ALL":
        current_lot_price = get_latest_ticker_price_for_symbol(con, lot_symbol_filter)

    top1, top2, top3 = st.columns(3)
    top1.metric(
        "Open lot rows",
        int(len(df_lots[df_lots["status"] == "OPEN"])) if not df_lots.empty and "status" in df_lots.columns else 0,
    )
    top2.metric("Realizations", int(len(df_real)) if not df_real.empty else 0)
    top3.metric("Current price", f"{current_lot_price:.6f}" if current_lot_price is not None else "-")

    if not df_open_lot_summary.empty:
        for col in ["open_lot_rows", "qty_opened_total", "qty_remaining_total", "min_entry_price", "max_entry_price"]:
            _to_float_series(df_open_lot_summary, col)

        st.write("### Open lots by symbol")
        summary_cols = [
            "symbol",
            "side",
            "open_lot_rows",
            "qty_opened_total",
            "qty_remaining_total",
            "min_entry_price",
            "max_entry_price",
            "latest_opened_at",
        ]
        summary_cols = [c for c in summary_cols if c in df_open_lot_summary.columns]
        st.dataframe(df_open_lot_summary[summary_cols], width="stretch", hide_index=True)

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
            "source_action_status",
            "source_panel_mode",
            "source_order_kind",
            "source_created_by",
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
                    "source_action_id",
                    "source_action_status",
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
            "close_action_status",
            "close_panel_mode",
            "close_order_kind",
            "close_created_by",
            "close_task_type",
            "close_task_id",
            "note",
        ]
        real_cols = [c for c in real_cols if c in df_real.columns]
        st.dataframe(df_real[real_cols], width="stretch", hide_index=True)
    else:
        st.info("No rows in lot_realizations for the selected filter.")

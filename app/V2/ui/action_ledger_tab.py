from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.action_ledger_service import (
    add_action_ledger,
    enrich_action_ledger_rows,
    get_action_ledger_summary,
    list_action_ledger_types,
    query_action_ledger,
    summarize_action_ledger_timeline,
)
from core.streamlit_services.common import now_utc_iso


def render_action_ledger_tab(con) -> None:
    st.subheader("Action Ledger")
    st.caption("Manual notes plus automatic queue, executor, reconcile, and reprice events for the active V2 flow.")

    summary = get_action_ledger_summary(con)
    s1, s2, s3 = st.columns(3)
    s1.metric("Total rows", summary.get("total_rows", 0))
    s2.metric("System events", summary.get("system_rows", 0))
    s3.metric("Manual entries", summary.get("manual_rows", 0))

    f1, f2, f3, f4 = st.columns(4)
    symbol_filter = f1.text_input("Symbol filter", value="", key="v2_action_ledger_symbol_filter").strip()
    action_types = ["ALL", *list_action_ledger_types(con)]
    action_type_filter = f2.selectbox("Action type", action_types, index=0, key="v2_action_ledger_type_filter")
    source_filter = f3.selectbox("Source", ["ALL", "SYSTEM", "MANUAL"], index=0, key="v2_action_ledger_source_filter")
    limit_filter = f4.number_input("Rows", min_value=10, max_value=500, value=100, step=10, key="v2_action_ledger_rows")

    with st.form("v2_add_action_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns(4)
        symbol_in = col1.text_input("Symbol", value="ADA/USDT:USDT")
        action_type_in = col2.selectbox("Action type", ["NOTE", "TRIM_LONG", "TRIM_SHORT", "HEDGE_OPEN", "HEDGE_CLOSE"])
        side_in = col3.selectbox("Side (optional)", ["", "LONG", "SHORT"])
        qty_in = col4.number_input("Qty (optional)", min_value=0.0, value=0.0, step=1.0)

        col5, col6 = st.columns(2)
        price_in = col5.number_input("Price (optional)", min_value=0.0, value=0.0, step=0.0001, format="%.6f")
        note_in = col6.text_input("Note (optional)", value="")

        submitted = st.form_submit_button("Add ledger entry")
        if submitted:
            add_action_ledger(
                con,
                created_at=now_utc_iso(),
                symbol=symbol_in.strip(),
                action_type=action_type_in,
                side=side_in if side_in else None,
                qty=qty_in if qty_in > 0 else None,
                price=price_in if price_in > 0 else None,
                note=note_in.strip() if note_in.strip() else None,
            )
            con.commit()
            st.success("Ledger entry saved.")
            st.rerun()

    actions = query_action_ledger(
        con,
        symbol=symbol_filter or None,
        action_type=action_type_filter,
        source=source_filter,
        limit=int(limit_filter),
    )

    if not actions:
        st.caption("No ledger rows for the current filter.")
        return

    enriched_actions = enrich_action_ledger_rows(actions)
    df_actions = pd.DataFrame(enriched_actions)

    timeline_rows = summarize_action_ledger_timeline(enriched_actions)
    if timeline_rows:
        st.write("### Timeline by action_id")
        df_timeline = pd.DataFrame(timeline_rows)
        timeline_cols = [
            "action_id",
            "symbol",
            "events",
            "first_at",
            "last_at",
            "latest_event_type",
            "latest_status",
            "latest_lifecycle_state",
            "latest_stage",
            "latest_event_kind",
            "latest_reason",
        ]
        timeline_cols = [c for c in timeline_cols if c in df_timeline.columns]
        st.dataframe(df_timeline[timeline_cols], width="stretch", hide_index=True)

    st.write("### Raw events")
    raw_cols = [
        "id",
        "created_at",
        "symbol",
        "action_type",
        "side",
        "qty",
        "price",
        "note_action_id",
        "note_status",
        "note_lifecycle_state",
        "note_stage",
        "note_event_kind",
        "note_reason",
        "note_lot_id",
        "note_matched_close_qty",
        "note_unmatched_close_qty",
        "note_previous_order_id",
        "note_new_order_id",
        "note",
    ]
    raw_cols = [c for c in raw_cols if c in df_actions.columns]
    st.dataframe(df_actions[raw_cols], width="stretch", hide_index=True)

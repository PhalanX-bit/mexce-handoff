from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.queue_health_service import (
    get_queue_health_summary,
    list_recent_reprice_rows,
    list_recent_reconcile_rows,
    list_rows_with_errors,
    list_running_or_armed_rows,
    list_rows_with_reprice_payload,
)


def render_queue_health_tab(con) -> None:
    st.subheader("Queue Health / Execution Health")
    st.caption("Operational view for reprice, reconcile, errors, and stuck or active rows.")

    summary = get_queue_health_summary(con)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("ARMED", summary.get("armed_count", 0))
    c2.metric("RUNNING", summary.get("running_count", 0))
    c3.metric("With errors", summary.get("error_count", 0))
    c4.metric("With reprice payload", summary.get("reprice_payload_count", 0))
    c5.metric("OPEN reconcile", summary.get("reconcile_open_count", 0))
    c6.metric("NOT_FOUND reconcile", summary.get("reconcile_not_found_count", 0))

    st.divider()

    f1, f2, f3 = st.columns(3)
    rows_limit = f1.number_input("Rows per section", min_value=10, value=50, step=10, key="v2_queue_health_limit")
    if f2.button("Refresh queue health", key="v2_refresh_queue_health"):
        st.rerun()
    show_full_payload = f3.checkbox("Show full JSON payload columns", value=False, key="v2_show_full_health_json")

    # Active / possibly stuck
    st.write("### Active rows (ARMED / RUNNING)")
    active_rows = list_running_or_armed_rows(con, limit=int(rows_limit))
    if active_rows:
        df_active = pd.DataFrame(active_rows)
        active_cols = [
            "id",
            "created_at",
            "symbol",
            "panel_mode",
            "order_kind",
            "side",
            "qty",
            "limit_price",
            "status",
            "attempts",
            "api_order_id",
            "api_mode",
            "last_error",
            "last_update_at",
            "ops_lock_token",
            "ops_locked_at",
            "note",
        ]
        active_cols = [c for c in active_cols if c in df_active.columns]
        st.dataframe(df_active[active_cols], width="stretch", hide_index=True)
    else:
        st.info("No ARMED/RUNNING rows.")

    # Errors
    st.write("### Rows with errors")
    error_rows = list_rows_with_errors(con, limit=int(rows_limit))
    if error_rows:
        df_err = pd.DataFrame(error_rows)
        err_cols = [
            "id",
            "created_at",
            "symbol",
            "panel_mode",
            "order_kind",
            "side",
            "qty",
            "limit_price",
            "status",
            "attempts",
            "api_order_id",
            "api_mode",
            "last_error",
            "last_update_at",
            "note",
        ]
        err_cols = [c for c in err_cols if c in df_err.columns]
        st.dataframe(df_err[err_cols], width="stretch", hide_index=True)
    else:
        st.info("No rows with last_error.")

    # Recent reprice
    st.write("### Recent reprice rows")
    reprice_rows = list_recent_reprice_rows(con, limit=int(rows_limit))
    if reprice_rows:
        df_rep = pd.DataFrame(reprice_rows)
        rep_cols = [
            "id",
            "created_at",
            "symbol",
            "status",
            "panel_mode",
            "side",
            "qty",
            "limit_price",
            "api_order_id",
            "api_client_oid",
            "api_mode",
            "last_update_at",
            "note",
        ]
        if show_full_payload:
            rep_cols += ["last_reprice_payload"]
        rep_cols = [c for c in rep_cols if c in df_rep.columns]
        st.dataframe(df_rep[rep_cols], width="stretch", hide_index=True)
    else:
        st.info("No recent reprice rows.")

    # Recent reconcile
    st.write("### Recent reconcile rows")
    reconcile_rows = list_recent_reconcile_rows(con, limit=int(rows_limit))
    if reconcile_rows:
        df_rec = pd.DataFrame(reconcile_rows)
        rec_cols = [
            "id",
            "created_at",
            "symbol",
            "status",
            "api_order_id",
            "reconcile_state",
            "reconcile_reason",
            "reconcile_checked_at",
            "last_update_at",
            "note",
        ]
        if show_full_payload:
            rec_cols += ["reconcile_payload"]
        rec_cols = [c for c in rec_cols if c in df_rec.columns]
        st.dataframe(df_rec[rec_cols], width="stretch", hide_index=True)
    else:
        st.info("No recent reconcile rows.")

    # Payload inspection
    st.write("### Rows with stored reprice payload")
    payload_rows = list_rows_with_reprice_payload(con, limit=int(rows_limit))
    if payload_rows:
        df_payload = pd.DataFrame(payload_rows)
        payload_cols = [
            "id",
            "created_at",
            "symbol",
            "status",
            "panel_mode",
            "side",
            "qty",
            "limit_price",
            "api_order_id",
            "api_mode",
            "last_update_at",
        ]
        if show_full_payload:
            payload_cols += ["last_reprice_payload"]
        payload_cols = [c for c in payload_cols if c in df_payload.columns]
        st.dataframe(df_payload[payload_cols], width="stretch", hide_index=True)

        selected_id = st.number_input(
            "Inspect action_queue row id",
            min_value=1,
            value=int(df_payload["id"].iloc[0]),
            step=1,
            key="v2_queue_health_selected_id",
        )

        row = df_payload[df_payload["id"] == selected_id]
        if not row.empty:
            st.markdown("**Selected row payloads**")
            selected = row.iloc[0].to_dict()

            left, right = st.columns(2)
            with left:
                st.write("last_reprice_payload")
                st.code(str(selected.get("last_reprice_payload") or "—"), language="json")
            with right:
                st.write("reconcile_payload")
                st.code(str(selected.get("reconcile_payload") or "—"), language="json")
    else:
        st.info("No rows with last_reprice_payload.")
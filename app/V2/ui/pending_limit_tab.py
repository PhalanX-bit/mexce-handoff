from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.pending_limit_service import (
    delete_pending_limit_task,
    delete_orphaned_resolved_pending_limit_tasks,
    delete_resolved_pending_limit_tasks,
    get_pending_limit_audit_counts,
    get_pending_limit_status_counts,
    list_pending_limit_tasks,
    mark_pending_limit_task_status,
    normalize_pending_limit_task_symbols,
    reset_pending_limit_task,
    set_pending_limit_created_now,
)


def render_pending_limit_tab(con) -> None:
    st.subheader("Legacy LIMIT tracking")
    st.caption(
        "Legacy compatibility / historical tracking only. "
        "Active V2 flow should use Action Queue, API Executor, and Lots instead of this table."
    )

    counts = get_pending_limit_status_counts(con)
    audit = get_pending_limit_audit_counts(con)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("PENDING", counts.get("PENDING", 0))
    m2.metric("TRIGGERED", counts.get("TRIGGERED", 0))
    m3.metric("FILLED", counts.get("FILLED", 0))
    m4.metric("EXPIRED", counts.get("EXPIRED", 0))
    m5.metric("FAILED_AFTER_TRIGGER", counts.get("FAILED_AFTER_TRIGGER", 0))

    a1, a2, a3 = st.columns(3)
    a1.metric("Total rows", audit.get("total_rows", 0))
    a2.metric("Orphan rows", audit.get("orphan_rows", 0))
    a3.metric("Orphan FAILED_AFTER_TRIGGER", audit.get("orphan_failed_after_trigger_rows", 0))

    status_options = [
        "ALL",
        "PENDING",
        "TRIGGERED",
        "FILLED",
        "EXPIRED",
        "FAILED_AFTER_TRIGGER",
    ]

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        pending_status_filter = st.selectbox(
            "Status filter",
            status_options,
            index=0,
            key="v2_pending_status_filter",
        )

    with col2:
        pending_limit_rows = list_pending_limit_tasks(con, status=pending_status_filter, limit=200)
        pending_ids = [int(r["id"]) for r in pending_limit_rows] if pending_limit_rows else []
        selected_task_id = st.selectbox(
            "Select task id",
            pending_ids if pending_ids else [0],
            key="v2_selected_pending_limit_task_id",
        )

    with col3:
        if st.button("Refresh pending LIMIT tasks", key="v2_refresh_pending_limit_tasks"):
            st.rerun()

    col4, col5, col6, col6b, col6c = st.columns([1, 1, 1, 1, 1])

    with col4:
        if st.button("Reset selected task", key="v2_reset_selected_pending_limit_task"):
            if selected_task_id:
                n = reset_pending_limit_task(con, int(selected_task_id))
                if n:
                    st.success(f"Reset task id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col5:
        if st.button("Set created_at=now", key="v2_set_created_now_pending_limit_task"):
            if selected_task_id:
                n = set_pending_limit_created_now(con, int(selected_task_id))
                if n:
                    st.success(f"Updated created_at for task id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col6:
        if st.button("Delete resolved tasks", key="v2_delete_resolved_pending_limit_tasks"):
            n = delete_resolved_pending_limit_tasks(con)
            st.success(f"Deleted resolved tasks: {n}")
            st.rerun()

    with col6b:
        if st.button("Normalize symbols", key="v2_normalize_pending_limit_symbols"):
            n = normalize_pending_limit_task_symbols(con)
            st.success(f"Normalized pending LIMIT task symbols: {n}")
            st.rerun()

    with col6c:
        if st.button("Delete orphan resolved", key="v2_delete_orphan_resolved_pending_limit_tasks"):
            n = delete_orphaned_resolved_pending_limit_tasks(con)
            st.success(f"Deleted orphan resolved pending LIMIT tasks: {n}")
            st.rerun()

    col7, col8, col9 = st.columns([1, 1, 1])

    with col7:
        if st.button("Mark selected FILLED", key="v2_mark_selected_pending_limit_filled"):
            if selected_task_id:
                n = mark_pending_limit_task_status(
                    con,
                    int(selected_task_id),
                    "FILLED",
                    " | manual_mark_filled",
                )
                if n:
                    st.success(f"Marked FILLED: id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col8:
        if st.button("Mark selected FAILED", key="v2_mark_selected_pending_limit_failed"):
            if selected_task_id:
                n = mark_pending_limit_task_status(
                    con,
                    int(selected_task_id),
                    "FAILED_AFTER_TRIGGER",
                    " | manual_mark_failed",
                )
                if n:
                    st.success(f"Marked FAILED_AFTER_TRIGGER: id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col9:
        if st.button("Delete selected task", key="v2_delete_selected_pending_limit_task"):
            if selected_task_id:
                n = delete_pending_limit_task(con, int(selected_task_id))
                if n:
                    st.success(f"Deleted task id={int(selected_task_id)}")
                else:
                    st.warning("No task deleted")
                st.rerun()

    if pending_limit_rows:
        df_pending = pd.DataFrame([dict(r) for r in pending_limit_rows])

        display_cols = [
            "id",
            "action_id",
            "action_exists",
            "action_status",
            "symbol",
            "side",
            "panel_mode",
            "limit_price",
            "qty",
            "status",
            "trigger_seen",
            "baseline_contracts",
            "triggered_at",
            "attempt_count",
            "created_at",
            "updated_at",
            "resolved_at",
            "note",
        ]
        display_cols = [c for c in display_cols if c in df_pending.columns]

        st.dataframe(
            df_pending[display_cols],
            width="stretch",
            hide_index=True,
        )

        if selected_task_id:
            selected_row = df_pending[df_pending["id"] == selected_task_id]
            if not selected_row.empty:
                st.markdown("**Selected task details**")
                st.json(selected_row.iloc[0].to_dict())
    else:
        st.info("No pending_limit_tasks rows for the selected filter.")

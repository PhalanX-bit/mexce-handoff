from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.pending_chase_service import (
    delete_pending_chase_task,
    delete_resolved_pending_chase_tasks,
    get_pending_chase_status_counts,
    list_pending_chase_tasks,
    mark_pending_chase_task_status,
    normalize_pending_chase_task_symbols,
    reset_pending_chase_task,
    set_pending_chase_created_now,
)


def render_pending_chase_tab(con) -> None:
    st.subheader("Pending CHASE tasks (legacy)")
    st.caption("This tab is legacy/read-only support. New strategy flow should not create CHASE tasks anymore.")

    counts = get_pending_chase_status_counts(con)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("PENDING", counts.get("PENDING", 0))
    m2.metric("FILLED", counts.get("FILLED", 0))
    m3.metric("EXPIRED", counts.get("EXPIRED", 0))
    m4.metric("FAILED", counts.get("FAILED", 0))

    status_options = [
        "ALL",
        "PENDING",
        "FILLED",
        "EXPIRED",
        "FAILED",
    ]

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        pending_chase_status_filter = st.selectbox(
            "Status filter",
            status_options,
            index=0,
            key="v2_pending_chase_status_filter",
        )

    with col2:
        pending_chase_rows = list_pending_chase_tasks(con, status=pending_chase_status_filter, limit=200)
        pending_chase_ids = [int(r["id"]) for r in pending_chase_rows] if pending_chase_rows else []
        selected_chase_task_id = st.selectbox(
            "Select task id",
            pending_chase_ids if pending_chase_ids else [0],
            key="v2_selected_pending_chase_task_id",
        )

    with col3:
        if st.button("Refresh pending CHASE tasks", key="v2_refresh_pending_chase_tasks"):
            st.rerun()

    col4, col5, col6, col6b = st.columns([1, 1, 1, 1])

    with col4:
        if st.button("Reset selected CHASE", key="v2_reset_selected_pending_chase_task"):
            if selected_chase_task_id:
                n = reset_pending_chase_task(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Reset CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col5:
        if st.button("Set CHASE created_at=now", key="v2_set_created_now_pending_chase_task"):
            if selected_chase_task_id:
                n = set_pending_chase_created_now(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Updated created_at for CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col6:
        if st.button("Delete resolved CHASE", key="v2_delete_resolved_pending_chase_tasks"):
            n = delete_resolved_pending_chase_tasks(con)
            st.success(f"Deleted resolved CHASE tasks: {n}")
            st.rerun()

    with col6b:
        if st.button("Normalize symbols", key="v2_normalize_pending_chase_symbols"):
            n = normalize_pending_chase_task_symbols(con)
            st.success(f"Normalized pending CHASE task symbols: {n}")
            st.rerun()

    col7, col8, col9 = st.columns([1, 1, 1])

    with col7:
        if st.button("Mark selected CHASE FILLED", key="v2_mark_selected_pending_chase_filled"):
            if selected_chase_task_id:
                n = mark_pending_chase_task_status(
                    con,
                    int(selected_chase_task_id),
                    "FILLED",
                    " | manual_mark_filled",
                )
                if n:
                    st.success(f"Marked CHASE FILLED: id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col8:
        if st.button("Mark selected CHASE FAILED", key="v2_mark_selected_pending_chase_failed"):
            if selected_chase_task_id:
                n = mark_pending_chase_task_status(
                    con,
                    int(selected_chase_task_id),
                    "FAILED",
                    " | manual_mark_failed",
                )
                if n:
                    st.success(f"Marked CHASE FAILED: id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col9:
        if st.button("Delete selected CHASE", key="v2_delete_selected_pending_chase_task"):
            if selected_chase_task_id:
                n = delete_pending_chase_task(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Deleted CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task deleted")
                st.rerun()

    if pending_chase_rows:
        df_pending_chase = pd.DataFrame([dict(r) for r in pending_chase_rows])

        display_cols = [
            "id",
            "action_id",
            "symbol",
            "side",
            "panel_mode",
            "qty",
            "status",
            "baseline_contracts",
            "filled_contracts",
            "created_at",
            "updated_at",
            "resolved_at",
            "note",
        ]
        display_cols = [c for c in display_cols if c in df_pending_chase.columns]

        st.dataframe(
            df_pending_chase[display_cols],
            width="stretch",
            hide_index=True,
        )

        if selected_chase_task_id:
            selected_row = df_pending_chase[df_pending_chase["id"] == selected_chase_task_id]
            if not selected_row.empty:
                st.markdown("**Selected CHASE task details**")
                st.json(selected_row.iloc[0].to_dict())
    else:
        st.info("No pending_chase_tasks rows for the selected filter.")

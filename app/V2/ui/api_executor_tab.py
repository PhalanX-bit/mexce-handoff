from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from core.api_executor import process_one_action
from core.db import DB_PATH


def _pretty(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def _get_armed_actions(con, symbol_filter: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if symbol_filter:
        rows = con.execute(
            """
            SELECT id, created_at, created_by, symbol, panel_mode, side, qty, limit_price,
                   order_kind, leverage, status, priority
            FROM action_queue
            WHERE status = 'ARMED'
              AND UPPER(symbol) = UPPER(?)
            ORDER BY priority ASC, id ASC
            LIMIT ?
            """,
            (symbol_filter, int(limit)),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, created_at, created_by, symbol, panel_mode, side, qty, limit_price,
                   order_kind, leverage, status, priority
            FROM action_queue
            WHERE status = 'ARMED'
            ORDER BY priority ASC, id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    return [dict(r) for r in rows]


def _get_next_armed_action(con, symbol_filter: str | None = None) -> dict[str, Any] | None:
    rows = _get_armed_actions(con, symbol_filter=symbol_filter, limit=1)
    return rows[0] if rows else None


def _get_recent_executor_rows(
    con,
    symbol_filter: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if symbol_filter:
        rows = con.execute(
            """
            SELECT id, created_at, symbol, panel_mode, side, qty, limit_price,
                   order_kind, leverage, status, api_order_id, api_client_oid,
                   api_submit_path, api_mode, last_error, last_update_at
            FROM action_queue
            WHERE (
                    api_mode = 'direct_futures_api'
                 OR api_mode = 'direct_futures_api_replace'
                 OR status IN ('FAILED', 'RUNNING', 'DONE')
                  )
              AND UPPER(symbol) = UPPER(?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol_filter, int(limit)),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, created_at, symbol, panel_mode, side, qty, limit_price,
                   order_kind, leverage, status, api_order_id, api_client_oid,
                   api_submit_path, api_mode, last_error, last_update_at
            FROM action_queue
            WHERE api_mode = 'direct_futures_api'
               OR api_mode = 'direct_futures_api_replace'
               OR status IN ('FAILED', 'RUNNING', 'DONE')
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    return [dict(r) for r in rows]


def render_api_executor_tab(con) -> None:
    st.subheader("API Executor")
    st.caption("Manual helper for direct API execution of ARMED queue rows.")

    c1, c2, c3 = st.columns(3)
    c1.metric("DB path", str(Path(DB_PATH).name))
    armed_count = con.execute(
        "SELECT COUNT(*) AS cnt FROM action_queue WHERE status = 'ARMED'"
    ).fetchone()["cnt"]
    running_count = con.execute(
        "SELECT COUNT(*) AS cnt FROM action_queue WHERE status = 'RUNNING'"
    ).fetchone()["cnt"]
    c2.metric("ARMED", int(armed_count))
    c3.metric("RUNNING", int(running_count))

    st.write("### Executor scope")
    scope_col1, scope_col2 = st.columns([2, 1])
    symbol_filter = scope_col1.text_input(
        "Symbol filter (optional)",
        value="",
        key="v2_api_executor_symbol_filter",
        help="Example: ADA/USDT:USDT. Leave blank to see all ARMED rows.",
    ).strip()
    recent_limit = scope_col2.number_input(
        "Recent rows limit",
        min_value=5,
        max_value=100,
        value=20,
        step=5,
        key="v2_api_executor_recent_limit",
    )

    normalized_filter = symbol_filter or None

    st.write("### ARMED queue preview")
    armed_rows = _get_armed_actions(con, symbol_filter=normalized_filter, limit=50)

    if not armed_rows:
        st.info("No ARMED actions found for the current scope.")
    else:
        df_armed = pd.DataFrame(armed_rows)
        st.dataframe(df_armed, width="stretch", hide_index=True)

        next_row = armed_rows[0]
        st.write("### Next ARMED action (executor order)")
        st.dataframe([next_row], width="stretch", hide_index=True)

        st.caption("Executor order is now: lowest priority first, then lowest id.")

        action_ids = [int(row["id"]) for row in armed_rows]
        default_action_id = int(next_row["id"])

        selected_action_id = st.selectbox(
            "Action id to execute",
            options=action_ids,
            index=action_ids.index(default_action_id),
            key="v2_api_executor_selected_action_id",
        )

        selected_row = next((r for r in armed_rows if int(r["id"]) == int(selected_action_id)), None)
        if selected_row is not None and int(selected_action_id) != int(default_action_id):
            st.warning(
                f"You selected action_id={selected_action_id}, which is not the first executor candidate "
                f"(next by order is {default_action_id})."
            )

        if st.button("Run executor once", key="v2_api_executor_run_once"):
            try:
                result = process_one_action(action_id=int(selected_action_id), verbose=True)
                st.success(f"Executor finished for action_id={int(selected_action_id)}")
                st.code(_pretty(result), language="json")
                st.rerun()
            except Exception as exc:
                st.error(f"Executor failed for action_id={int(selected_action_id)}: {exc}")

    st.write("### Recent executor-related rows")
    recent_rows = _get_recent_executor_rows(
        con,
        symbol_filter=normalized_filter,
        limit=int(recent_limit),
    )
    if recent_rows:
        st.dataframe(recent_rows, width="stretch", hide_index=True)
    else:
        st.info("No recent executor-related rows.")
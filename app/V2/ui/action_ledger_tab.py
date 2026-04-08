from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.action_ledger_service import (
    add_action_ledger,
    get_recent_actions,
)
from core.streamlit_services.common import now_utc_iso


def render_action_ledger_tab(con) -> None:
    st.subheader("Action Ledger (manual notes / confirmations)")

    with st.form("v2_add_action_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns(4)
        symbol_in = col1.text_input("Symbol", value="ADA/USDT")
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

    actions = get_recent_actions(con, limit=50)
    if actions:
        df_actions = pd.DataFrame(actions)
        st.dataframe(df_actions, width="stretch")
    else:
        st.caption("No actions yet.")
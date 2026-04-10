from __future__ import annotations

# Active Streamlit entrypoint for ongoing V2 work on codex/v2-stabilization.

import sys
from pathlib import Path

import streamlit as st

# Ensure project root on sys.path so "core" imports work
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import connect  # noqa: E402
from core.streamlit_services.queue_service import get_db_files, reset_running  # noqa: E402

from app.V2.ui.action_ledger_tab import render_action_ledger_tab  # noqa: E402
from app.V2.ui.action_queue_tab import render_action_queue_tab  # noqa: E402
from app.V2.ui.api_executor_tab import render_api_executor_tab  # noqa: E402
from app.V2.ui.controls_tab import render_controls_tab  # noqa: E402
from app.V2.ui.dashboard_tab import render_dashboard_tab  # noqa: E402
from app.V2.ui.lots_tab import render_lots_tab  # noqa: E402
from app.V2.ui.market_symbol_tab import render_market_symbol_tab  # noqa: E402
from app.V2.ui.pending_chase_tab import render_pending_chase_tab  # noqa: E402
from app.V2.ui.pending_limit_tab import render_pending_limit_tab  # noqa: E402
from app.V2.ui.queue_health_tab import render_queue_health_tab  # noqa: E402
from app.V2.ui.strategy_simulation_tab import render_strategy_simulation_tab  # noqa: E402
from app.V2.ui.strategy_tab import render_strategy_tab  # noqa: E402


def _render_sidebar(con) -> None:
    with st.sidebar:
        st.subheader("Debug")
        if st.checkbox("Show DB file path(s)", value=True):
            st.write(get_db_files(con))

        st.divider()
        st.subheader("Emergency tools (DB)")
        st.caption("Use only if tasks are stuck RUNNING and block the Strategy guard.")

        symbol_to_reset = st.text_input("Symbol to reset (optional)", value="").strip()
        also_reset_armed = st.checkbox("Also reset ARMED", value=False)

        c1, c2 = st.columns(2)

        if c1.button("Reset RUNNING (symbol)"):
            if not symbol_to_reset:
                st.error("Enter a symbol (e.g. ADA/USDT:USDT).")
            else:
                updated = reset_running(
                    con,
                    symbol=symbol_to_reset,
                    also_reset_armed=also_reset_armed,
                )
                st.success(f"Reset {updated} rows for {symbol_to_reset}.")
                st.rerun()

        if c2.button("Reset RUNNING (ALL)"):
            updated = reset_running(
                con,
                symbol=None,
                also_reset_armed=also_reset_armed,
            )
            st.success(f"Reset {updated} rows (ALL symbols).")
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="MEXC Monitor V2", layout="wide")
    st.title("MEXC Monitor V2")

    con = connect()
    try:
        _render_sidebar(con)

        (
            tab_dashboard,
            tab_market_symbol,
            tab_action_queue,
            tab_action_ledger,
            tab_strategy,
            tab_strategy_simulation,
            tab_queue_health,
            tab_controls,
            tab_api_executor,
            tab_pending_limit,
            tab_pending_chase,
            tab_lots,
        ) = st.tabs(
            [
                "Dashboard",
                "Market / Symbol",
                "Action Queue",
                "Action Ledger",
                "Strategy (AUTO)",
                "Strategy Simulation",
                "Queue Health",
                "Controls",
                "API Executor",
                "Legacy LIMIT",
                "Legacy CHASE",
                "Lots",
            ]
        )

        with tab_dashboard:
            render_dashboard_tab(con)

        with tab_market_symbol:
            render_market_symbol_tab(con)

        with tab_action_queue:
            render_action_queue_tab(con)

        with tab_action_ledger:
            render_action_ledger_tab(con)

        with tab_strategy:
            render_strategy_tab(con)

        with tab_strategy_simulation:
            render_strategy_simulation_tab(con)

        with tab_queue_health:
            render_queue_health_tab(con)

        with tab_controls:
            render_controls_tab(con)

        with tab_api_executor:
            render_api_executor_tab(con)

        with tab_pending_limit:
            render_pending_limit_tab(con)

        with tab_pending_chase:
            render_pending_chase_tab(con)

        with tab_lots:
            render_lots_tab(con)

    finally:
        con.close()


if __name__ == "__main__":
    main()

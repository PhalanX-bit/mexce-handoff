from __future__ import annotations

import pandas as pd
import streamlit as st

from core.streamlit_services.market_symbol_service import (
    build_symbol_market_view,
    clear_symbol_market_view_cache,
)
from core.streamlit_services.symbol_picker_service import (
    load_futures_symbol_options,
    resolve_default_symbol_index,
)
from core.streamlit_services.table_columns_service import project_df_columns


def _display_value(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return str(value)
    if isinstance(value, (int, bool)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(x) for x in value)
    if isinstance(value, dict):
        return str(value)
    return str(value)


def _rows_to_display_df(rows: list[dict]) -> pd.DataFrame:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "field": str(row.get("field", "")),
                "value": _display_value(row.get("value")),
            }
        )
    return pd.DataFrame(normalized)


def _render_compact_metric_css() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stMetric"] label {
            font-size: 0.80rem !important;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.05rem !important;
        }
        div[data-testid="stMetric"] {
            padding-top: 0.15rem !important;
            padding-bottom: 0.15rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_dataframe_section(title: str, df, empty_message: str, table_key: str | None = None) -> None:
    st.write(f"### {title}")
    if df is not None and not df.empty:
        if table_key:
            df = project_df_columns(df, table_key)
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info(empty_message)


def _render_symbol_summaries(result: dict) -> None:
    top = result.get("top_summary") or {}
    symbol_summary = result.get("symbol_summary") or {}
    health_summary = result.get("health_summary") or {}

    _render_compact_metric_css()

    m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)
    m1.metric("Last price", top.get("last_price", "—"))
    m2.metric("Mark price", top.get("mark_price", "—"))
    m3.metric("Index price", top.get("index_price", "—"))
    m4.metric("Bid / Ask", f'{top.get("bid_price", "—")} / {top.get("ask_price", "—")}')
    m5.metric("LONG", top.get("long_contracts", "0.00"))
    m6.metric("SHORT", top.get("short_contracts", "0.00"))
    m7.metric("Lots / Orders", f'{top.get("open_lots_count", 0)} / {top.get("live_open_orders_count", 0)}')
    m8.metric("Ticker time", top.get("ticker_created_at", "—"))

    st.write("### Symbol summary")

    left, right = st.columns(2)

    with left:
        summary_rows = [
            {"field": "symbol", "value": symbol_summary.get("symbol", "—")},
            {"field": "matched_ticker_symbol", "value": symbol_summary.get("matched_ticker_symbol", "—")},
            {"field": "ticker_created_at", "value": symbol_summary.get("ticker_created_at", "—")},
            {"field": "last_price_raw", "value": symbol_summary.get("last_price_raw", "—")},
            {"field": "mark_price_raw", "value": symbol_summary.get("mark_price_raw", "—")},
            {"field": "index_price_raw", "value": symbol_summary.get("index_price_raw", "—")},
            {"field": "bid_price_raw", "value": symbol_summary.get("bid_price_raw", "—")},
            {"field": "ask_price_raw", "value": symbol_summary.get("ask_price_raw", "—")},
            {"field": "price_tick_raw", "value": symbol_summary.get("price_tick_raw", "—")},
            {"field": "qty_step_raw", "value": symbol_summary.get("qty_step_raw", "—")},
            {"field": "min_qty_raw", "value": symbol_summary.get("min_qty_raw", "—")},
            {"field": "max_leverage_raw", "value": symbol_summary.get("max_leverage_raw", "—")},
            {"field": "long_contracts_raw", "value": symbol_summary.get("long_contracts_raw", 0)},
            {"field": "short_contracts_raw", "value": symbol_summary.get("short_contracts_raw", 0)},
            {"field": "open_lots_count_raw", "value": symbol_summary.get("open_lots_count_raw", 0)},
            {"field": "eligible_lots_count_raw", "value": symbol_summary.get("eligible_lots_count_raw", 0)},
        ]
        st.dataframe(_rows_to_display_df(summary_rows), width="stretch", hide_index=True)

    with right:
        health_rows = [
            {"field": "active_queue_rows", "value": health_summary.get("active_queue_rows", 0)},
            {"field": "recent_queue_rows", "value": health_summary.get("recent_queue_rows", 0)},
            {"field": "recent_reprice_rows", "value": health_summary.get("recent_reprice_rows", 0)},
            {"field": "recent_reconcile_rows", "value": health_summary.get("recent_reconcile_rows", 0)},
            {"field": "error_rows", "value": health_summary.get("error_rows", 0)},
            {"field": "live_open_orders", "value": health_summary.get("live_open_orders", 0)},
        ]
        st.dataframe(_rows_to_display_df(health_rows), width="stretch", hide_index=True)

    with st.expander("Price interpretation", expanded=False):
        st.write(
            "- Last / Mark / Index / Bid / Ask идват от live ticker path.\n"
            "- Contract meta е отделен и кеширан по-дълго.\n"
            "- Таблиците са олекотени до operational полета.\n"
            "- Debug/излишни derived полета са махнати от основния изглед."
        )


def _render_debug_json(result: dict) -> None:
    with st.expander("Debug JSON", expanded=False):
        st.write("top_summary")
        st.json(result.get("top_summary", {}))

        st.write("symbol_summary")
        st.json(result.get("symbol_summary", {}))

        st.write("health_summary")
        st.json(result.get("health_summary", {}))


def render_market_symbol_tab(con) -> None:
    st.subheader("Market / Symbol View")
    st.caption("Live market data + operational state for one futures symbol.")

    symbol_options = load_futures_symbol_options(con)

    c1, c2 = st.columns([3, 1])

    if not symbol_options:
        st.warning("No futures symbols found in DB yet.")
        return

    symbol = c1.selectbox(
        "Symbol",
        options=symbol_options,
        index=resolve_default_symbol_index(symbol_options, "BTC/USDT:USDT"),
        key="v2_market_symbol",
    )

    refresh = c2.button("Refresh", key="v2_market_symbol_refresh")

    if refresh:
        clear_symbol_market_view_cache(symbol)
        st.rerun()

    if not symbol:
        st.warning("Select a symbol.")
        return

    result = build_symbol_market_view(con=con, symbol=symbol)

    _render_symbol_summaries(result)

    _render_dataframe_section(
        "Positions",
        result.get("positions_df"),
        "No latest positions for this symbol.",
        table_key="market_positions",
    )

    _render_dataframe_section(
        "Live open orders",
        result.get("live_open_orders_df"),
        "No live open orders returned for this symbol.",
        table_key="market_live_open_orders",
    )

    lot_left, lot_right = st.columns(2)
    with lot_left:
        _render_dataframe_section(
            "Open lots",
            result.get("open_lots_df"),
            "No open lots for this symbol.",
            table_key="market_open_lots",
        )

    with lot_right:
        _render_dataframe_section(
            "Eligible lots now",
            result.get("eligible_lots_df"),
            "No eligible lots at current market price.",
            table_key="market_open_lots",
        )

    q1, q2 = st.columns(2)
    with q1:
        _render_dataframe_section(
            "Active queue rows",
            result.get("active_queue_df"),
            "No active ARMED/RUNNING queue rows for this symbol.",
            table_key="market_health_rows",
        )

    with q2:
        _render_dataframe_section(
            "Recent queue rows",
            result.get("recent_queue_df"),
            "No recent action_queue rows for this symbol.",
            table_key="market_recent_queue",
        )

    r1, r2 = st.columns(2)
    with r1:
        _render_dataframe_section(
            "Recent reprice rows",
            result.get("recent_reprice_df"),
            "No recent reprice rows for this symbol.",
            table_key="market_health_rows",
        )

    with r2:
        _render_dataframe_section(
            "Recent reconcile rows",
            result.get("recent_reconcile_df"),
            "No recent reconcile rows for this symbol.",
            table_key="market_health_rows",
        )

    _render_dataframe_section(
        "Rows with errors",
        result.get("error_rows_df"),
        "No error rows for this symbol.",
        table_key="market_health_rows",
    )

    _render_debug_json(result)
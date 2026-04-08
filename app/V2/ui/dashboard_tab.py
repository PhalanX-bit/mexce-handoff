from __future__ import annotations

import pandas as pd
import streamlit as st

from core.mexc_direct import get_live_market_snapshot
from core.streamlit_services.common import _to_float_series, risk_flag, suggest_next_action
from core.streamlit_services.dashboard_service import (
    get_latest_account_snapshot,
    get_latest_positions_per_symbol_side,
)
from core.streamlit_services.table_columns_service import project_df_columns


def _safe_live_snapshot(symbol: str) -> dict | None:
    try:
        return get_live_market_snapshot(symbol)
    except Exception:
        return None


def _build_live_market_df(symbols: list[str]) -> pd.DataFrame | None:
    rows: list[dict] = []

    seen = set()
    for symbol in symbols:
        s = str(symbol or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)

        snap = _safe_live_snapshot(s)
        if not snap:
            continue

        rows.append(
            {
                "symbol": s,
                "last_price": snap.get("last_price"),
                "mark_price": snap.get("mark_price"),
                "index_price": snap.get("index_price"),
                "bid_price": snap.get("bid_price"),
                "ask_price": snap.get("ask_price"),
                "ticker_at": snap.get("created_at"),
                "matched_ticker_symbol": snap.get("matched_ticker_symbol"),
            }
        )

    if not rows:
        return None

    df = pd.DataFrame(rows)
    _to_float_series(df, "last_price")
    _to_float_series(df, "mark_price")
    _to_float_series(df, "index_price")
    _to_float_series(df, "bid_price")
    _to_float_series(df, "ask_price")
    return df


def render_dashboard_tab(con) -> None:
    st.subheader("Live data (DB positions + live market API)")

    cA, cB, cC = st.columns([1, 1, 2])
    show_closed_legs = cA.checkbox("Show CLOSED legs (contracts=0)", value=False, key="v2_show_closed_legs")
    aggregates_open_only = cB.checkbox("Aggregates from OPEN legs only", value=True, key="v2_aggregates_open_only")
    cC.caption("Positions remain DB-based; market prices are fetched live from MEXC API.")

    acc = get_latest_account_snapshot(con)
    pos_ts, pos_rows = get_latest_positions_per_symbol_side(con)

    c1, c2, c3, c4 = st.columns(4)
    if acc:
        c1.metric("Equity (USDT)", f"{acc['equity']:.4f}" if acc.get("equity") is not None else "—")
        c2.metric("Free margin (USDT)", f"{acc['free_margin']:.4f}" if acc.get("free_margin") is not None else "—")
        c3.metric("Margin ratio", f"{acc['margin_ratio']:.4f}" if acc.get("margin_ratio") is not None else "—")
        c4.metric("Account snapshot (UTC)", acc.get("created_at", "—"))
    else:
        c1.metric("Equity (USDT)", "—")
        c2.metric("Free margin (USDT)", "—")
        c3.metric("Margin ratio", "—")
        c4.metric("Account snapshot (UTC)", "—")

    st.caption(f"Latest positions per (symbol, side) timestamp: {pos_ts or '—'}")

    if not pos_rows:
        st.info("No positions_snapshot data yet.")
        return

    df_pos = pd.DataFrame(pos_rows)

    _to_float_series(df_pos, "contracts")
    _to_float_series(df_pos, "entry_price")
    _to_float_series(df_pos, "unrealized_pnl")

    symbols = sorted({str(x).strip() for x in df_pos["symbol"].dropna().tolist() if str(x).strip()})
    df_live = _build_live_market_df(symbols)

    if df_live is not None and not df_live.empty:
        df_pos = df_pos.merge(
            df_live[
                [
                    "symbol",
                    "last_price",
                    "mark_price",
                    "index_price",
                    "bid_price",
                    "ask_price",
                    "ticker_at",
                    "matched_ticker_symbol",
                ]
            ],
            on="symbol",
            how="left",
        )
    else:
        df_pos["last_price"] = None
        df_pos["mark_price"] = None
        df_pos["index_price"] = None
        df_pos["bid_price"] = None
        df_pos["ask_price"] = None
        df_pos["ticker_at"] = None
        df_pos["matched_ticker_symbol"] = None

    df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
    df_pos["status"] = df_pos["contracts"].apply(lambda x: "OPEN" if float(x or 0.0) > 0 else "CLOSED")

    df_pos_view = df_pos[df_pos["status"] == "OPEN"].copy() if not show_closed_legs else df_pos.copy()

    df_pos_view["signed_contracts"] = df_pos_view.apply(
        lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
        axis=1,
    )

    df_for_agg = df_pos[df_pos["status"] == "OPEN"].copy() if aggregates_open_only else df_pos.copy()

    df_for_agg["signed_contracts"] = df_for_agg.apply(
        lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
        axis=1,
    )

    df_agg = (
        df_for_agg.groupby("symbol", as_index=False)
        .agg(
            total_unrealized=("unrealized_pnl", "sum"),
            net_contracts=("signed_contracts", "sum"),
            gross_contracts=("contracts", "sum"),
        )
        .sort_values("total_unrealized")
    )

    equity_val = acc.get("equity") if acc and acc.get("equity") else None
    df_agg["risk_flag"] = df_agg.apply(lambda r: risk_flag(r, equity_val), axis=1)
    df_agg["suggested_next_action"] = df_agg.apply(suggest_next_action, axis=1)

    left, right = st.columns([2, 1])

    with left:
        st.write("### Positions (latest per symbol+side)")
        st.dataframe(
            project_df_columns(df_pos_view, "dashboard_positions"),
            width="stretch",
            hide_index=True,
        )

    with right:
        st.write("### Aggregates by symbol (risk + suggestion)")
        st.dataframe(
            project_df_columns(df_agg, "dashboard_aggregates"),
            width="stretch",
            hide_index=True,
        )

    losers = project_df_columns(df_agg.nsmallest(5, "total_unrealized"), "dashboard_aggregates")
    winners = project_df_columns(df_agg.nlargest(5, "total_unrealized"), "dashboard_aggregates")

    l1, l2 = st.columns(2)
    with l1:
        st.write("### Top losers (unrealized)")
        st.dataframe(losers, width="stretch", hide_index=True)
    with l2:
        st.write("### Top winners (unrealized)")
        st.dataframe(winners, width="stretch", hide_index=True)

    st.write("### Suggested next actions (per symbol)")
    for _, r in df_agg.iterrows():
        st.write(f"**{r['symbol']}** — {r['risk_flag']}")
        st.write(r["suggested_next_action"])
        st.write("")
from __future__ import annotations

import copy
import time

import pandas as pd

from core.mexc_direct import (
    futures_symbol_raw,
    get_contract_meta,
    get_live_ticker_snapshot,
    list_open_orders_raw,
)
from core.streamlit_services.lots_service import (
    compute_eligible_lots_df,
    get_open_lots_for_symbol,
)
from core.streamlit_services.queue_health_service import (
    list_recent_reconcile_rows_for_symbols,
    list_recent_reprice_rows_for_symbols,
    list_rows_with_errors_for_symbols,
    list_running_or_armed_rows_for_symbols,
)

_SYMBOL_MARKET_VIEW_CACHE: dict[str, tuple[float, dict]] = {}
_SYMBOL_MARKET_VIEW_TTL_SEC = 2.0


def clear_symbol_market_view_cache(symbol: str | None = None) -> None:
    global _SYMBOL_MARKET_VIEW_CACHE

    if not symbol:
        _SYMBOL_MARKET_VIEW_CACHE.clear()
        return

    aliases = _build_symbol_aliases(symbol)
    keys_to_delete = []
    for key in list(_SYMBOL_MARKET_VIEW_CACHE.keys()):
        if key in aliases:
            keys_to_delete.append(key)

    for key in keys_to_delete:
        _SYMBOL_MARKET_VIEW_CACHE.pop(key, None)


def _get_cached_symbol_market_view(symbol: str) -> dict | None:
    key = str(symbol or "").strip().upper()
    if not key:
        return None

    entry = _SYMBOL_MARKET_VIEW_CACHE.get(key)
    if not entry:
        return None

    ts, value = entry
    if (time.monotonic() - ts) > _SYMBOL_MARKET_VIEW_TTL_SEC:
        _SYMBOL_MARKET_VIEW_CACHE.pop(key, None)
        return None

    return copy.deepcopy(value)


def _set_cached_symbol_market_view(symbol: str, value: dict) -> None:
    key = str(symbol or "").strip().upper()
    if not key:
        return
    _SYMBOL_MARKET_VIEW_CACHE[key] = (time.monotonic(), copy.deepcopy(value))


def _df_or_none(rows) -> pd.DataFrame | None:
    if not rows:
        return None

    df = pd.DataFrame(rows)
    return df if not df.empty else None


def _safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_live_open_orders(symbol: str):
    try:
        return list_open_orders_raw(symbol)
    except Exception:
        return []


def _safe_live_ticker_snapshot(symbol: str):
    try:
        return get_live_ticker_snapshot(symbol)
    except Exception:
        return None


def _safe_contract_meta(symbol: str):
    try:
        return get_contract_meta(symbol)
    except Exception:
        return None


def _build_symbol_aliases(symbol: str) -> set[str]:
    raw = str(symbol or "").strip()
    if not raw:
        return set()

    aliases = {raw}
    upper_raw = raw.upper()
    aliases.add(upper_raw)

    if ":" in upper_raw:
        aliases.add(upper_raw.split(":", 1)[0])

    try:
        aliases.add(futures_symbol_raw(upper_raw))
    except Exception:
        pass

    if "_" in upper_raw:
        parts = upper_raw.split("_", 1)
        if len(parts) == 2:
            aliases.add(f"{parts[0]}/{parts[1]}")
            aliases.add(f"{parts[0]}/{parts[1]}:USDT")

    if "/" in upper_raw and ":" not in upper_raw:
        aliases.add(f"{upper_raw}:USDT")

    return {x for x in aliases if x}


def _compute_position_totals(df_positions: pd.DataFrame | None) -> tuple[float, float]:
    long_contracts = 0.0
    short_contracts = 0.0

    if df_positions is None or df_positions.empty:
        return long_contracts, short_contracts

    df = df_positions.copy()
    df["side"] = df["side"].fillna("").astype(str).str.upper()

    if "contracts" in df.columns:
        df["contracts"] = pd.to_numeric(df["contracts"], errors="coerce").fillna(0.0)

    long_rows = df[df["side"] == "LONG"]
    short_rows = df[df["side"] == "SHORT"]

    if not long_rows.empty:
        long_contracts = float(long_rows["contracts"].sum())

    if not short_rows.empty:
        short_contracts = float(short_rows["contracts"].sum())

    return long_contracts, short_contracts


def _fetch_latest_positions_for_aliases(con, aliases: list[str]) -> list[dict]:
    if not aliases:
        return []

    placeholders = ",".join("?" for _ in aliases)
    sql = f"""
        SELECT p.symbol, p.side, p.contracts, p.entry_price, p.unrealized_pnl, p.created_at
        FROM positions_snapshot p
        JOIN (
            SELECT symbol, side, MAX(id) AS max_id
            FROM positions_snapshot
            WHERE UPPER(symbol) IN ({placeholders})
            GROUP BY symbol, side
        ) latest
        ON p.id = latest.max_id
        ORDER BY p.symbol ASC, p.side ASC
    """
    rows = con.execute(sql, tuple(aliases)).fetchall()
    return [dict(r) for r in rows]


def _fetch_recent_queue_rows_for_aliases(con, aliases: list[str], limit: int = 20) -> list[dict]:
    if not aliases:
        return []

    placeholders = ",".join("?" for _ in aliases)
    sql = f"""
        SELECT id, symbol, panel_mode, order_kind, side, qty, limit_price,
               status, priority, last_update_at, note
        FROM action_queue
        WHERE UPPER(symbol) IN ({placeholders})
        ORDER BY id DESC
        LIMIT ?
    """
    rows = con.execute(sql, tuple(aliases) + (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def _fetch_open_lots_for_aliases(con, aliases: list[str]) -> list[dict]:
    if not aliases:
        return []

    out = []
    seen_ids = set()

    for alias in aliases:
        try:
            rows = get_open_lots_for_symbol(con, alias)
        except Exception:
            rows = []

        for row in rows:
            row_id = row.get("id")
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            out.append(
                {
                    "id": row.get("id"),
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "qty_remaining": row.get("qty_remaining"),
                    "entry_price": row.get("entry_price"),
                    "target_price": row.get("target_price"),
                    "opened_at": row.get("opened_at"),
                    "status": row.get("status"),
                }
            )

    out.sort(key=lambda r: r.get("id", 0), reverse=True)
    return out[:50]


def _trim_live_open_orders(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows[:30]:
        out.append(
            {
                "orderId": row.get("orderId"),
                "symbol": row.get("symbol"),
                "price": row.get("price"),
                "vol": row.get("vol"),
                "dealVol": row.get("dealVol"),
                "side": row.get("side"),
                "state": row.get("state"),
                "category": row.get("category"),
                "createTime": row.get("createTime"),
            }
        )
    return out


def _trim_health_rows(rows: list[dict], limit: int = 20) -> list[dict]:
    out = []
    for row in rows[:limit]:
        out.append(
            {
                "id": row.get("id"),
                "symbol": row.get("symbol"),
                "panel_mode": row.get("panel_mode"),
                "order_kind": row.get("order_kind"),
                "side": row.get("side"),
                "status": row.get("status"),
                "reconcile_state": row.get("reconcile_state"),
                "decision_reason": row.get("decision_reason"),
                "last_error": row.get("last_error"),
                "last_update_at": row.get("last_update_at"),
            }
        )
    return out


def build_symbol_market_view(*, con, symbol: str) -> dict:
    cached = _get_cached_symbol_market_view(symbol)
    if cached is not None:
        return cached

    aliases_set = _build_symbol_aliases(symbol)
    aliases = sorted(str(x).upper() for x in aliases_set if x)

    live_ticker = _safe_live_ticker_snapshot(symbol)
    contract_meta = _safe_contract_meta(symbol)

    last_price = _safe_float((live_ticker or {}).get("last_price"))
    mark_price = _safe_float((live_ticker or {}).get("mark_price"))
    index_price = _safe_float((live_ticker or {}).get("index_price"))
    bid_price = _safe_float((live_ticker or {}).get("bid_price"))
    ask_price = _safe_float((live_ticker or {}).get("ask_price"))

    matched_ticker_symbol = (live_ticker or {}).get("matched_ticker_symbol") if live_ticker else None
    ticker_created_at = (live_ticker or {}).get("created_at") if live_ticker else None

    positions_rows = _fetch_latest_positions_for_aliases(con, aliases)
    df_positions = _df_or_none(positions_rows)

    long_contracts, short_contracts = _compute_position_totals(df_positions)

    open_lots = _fetch_open_lots_for_aliases(con, aliases)
    df_open_lots = _df_or_none(open_lots)

    lots_reference_price = last_price or mark_price or index_price
    eligible_lots_df = None
    if df_open_lots is not None and not df_open_lots.empty and lots_reference_price is not None:
        eligible_lots_df = compute_eligible_lots_df(df_open_lots, lots_reference_price)
        if eligible_lots_df is not None and not eligible_lots_df.empty:
            keep_cols = [
                "id",
                "symbol",
                "side",
                "qty_remaining",
                "entry_price",
                "target_price",
                "opened_at",
                "status",
            ]
            keep_cols = [c for c in keep_cols if c in eligible_lots_df.columns]
            eligible_lots_df = eligible_lots_df[keep_cols]
        else:
            eligible_lots_df = None

    active_queue_rows = _trim_health_rows(
        list_running_or_armed_rows_for_symbols(con, symbols=aliases, limit=20),
        limit=20,
    )
    recent_reprice_rows = _trim_health_rows(
        list_recent_reprice_rows_for_symbols(con, symbols=aliases, limit=20),
        limit=20,
    )
    recent_reconcile_rows = _trim_health_rows(
        list_recent_reconcile_rows_for_symbols(con, symbols=aliases, limit=20),
        limit=20,
    )
    error_rows = _trim_health_rows(
        list_rows_with_errors_for_symbols(con, symbols=aliases, limit=20),
        limit=20,
    )

    recent_queue_rows = _fetch_recent_queue_rows_for_aliases(con, aliases, limit=20)

    live_open_orders = _safe_live_open_orders(symbol)
    df_live_open_orders = _df_or_none(_trim_live_open_orders(live_open_orders))

    symbol_summary = {
        "symbol": symbol,
        "matched_ticker_symbol": matched_ticker_symbol,
        "ticker_created_at": ticker_created_at,
        "last_price_raw": last_price,
        "mark_price_raw": mark_price,
        "index_price_raw": index_price,
        "bid_price_raw": bid_price,
        "ask_price_raw": ask_price,
        "price_tick_raw": None if not contract_meta else contract_meta.get("price_tick"),
        "qty_step_raw": None if not contract_meta else contract_meta.get("qty_step"),
        "min_qty_raw": None if not contract_meta else contract_meta.get("min_qty"),
        "max_leverage_raw": None if not contract_meta else contract_meta.get("max_leverage"),
        "long_contracts_raw": long_contracts,
        "short_contracts_raw": short_contracts,
        "open_lots_count_raw": len(open_lots),
        "eligible_lots_count_raw": 0 if eligible_lots_df is None else len(eligible_lots_df),
    }

    health_summary = {
        "active_queue_rows": len(active_queue_rows),
        "recent_queue_rows": len(recent_queue_rows),
        "recent_reprice_rows": len(recent_reprice_rows),
        "recent_reconcile_rows": len(recent_reconcile_rows),
        "error_rows": len(error_rows),
        "live_open_orders": len(live_open_orders),
    }

    top_summary = {
        "last_price": "—" if last_price is None else f"{float(last_price):,.6f}",
        "mark_price": "—" if mark_price is None else f"{float(mark_price):,.6f}",
        "index_price": "—" if index_price is None else f"{float(index_price):,.6f}",
        "bid_price": "—" if bid_price is None else f"{float(bid_price):,.6f}",
        "ask_price": "—" if ask_price is None else f"{float(ask_price):,.6f}",
        "ticker_created_at": ticker_created_at or "—",
        "long_contracts": f"{long_contracts:,.2f}",
        "short_contracts": f"{short_contracts:,.2f}",
        "open_lots_count": int(len(open_lots)),
        "live_open_orders_count": int(len(live_open_orders)),
    }

    result = {
        "top_summary": top_summary,
        "symbol_summary": symbol_summary,
        "health_summary": health_summary,
        "positions_df": df_positions,
        "live_open_orders_df": df_live_open_orders,
        "open_lots_df": df_open_lots,
        "eligible_lots_df": eligible_lots_df,
        "active_queue_df": _df_or_none(active_queue_rows),
        "recent_queue_df": _df_or_none(recent_queue_rows),
        "recent_reprice_df": _df_or_none(recent_reprice_rows),
        "recent_reconcile_df": _df_or_none(recent_reconcile_rows),
        "error_rows_df": _df_or_none(error_rows),
    }

    _set_cached_symbol_market_view(symbol, result)
    return copy.deepcopy(result)
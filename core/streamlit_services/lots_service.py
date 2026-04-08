from __future__ import annotations

import pandas as pd
from core.streamlit_services.common import _to_float_series
from core.symbol_utils import build_symbol_aliases


def list_position_lots(con, symbol: str = "ALL", status: str = "ALL", limit: int = 500):
    sql = """
        SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
               target_roi_pct, leverage, target_price, opened_at,
               source_action_id, source_task_type, source_task_id, status
        FROM position_lots
    """
    clauses = []
    params = []

    if symbol != "ALL":
        aliases = build_symbol_aliases(symbol)
        if aliases:
            placeholders = ",".join("?" for _ in aliases)
            clauses.append(f"UPPER(symbol) IN ({placeholders})")
            params.extend(aliases)
        else:
            clauses.append("symbol = ?")
            params.append(symbol)

    if status != "ALL":
        clauses.append("status = ?")
        params.append(status)

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_lot_realizations(con, symbol: str = "ALL", limit: int = 500):
    sql = """
        SELECT id, lot_id, symbol, side, close_qty, entry_price, close_price,
               target_price, realized_roi_pct, closed_at,
               close_action_id, close_task_type, close_task_id, note
        FROM lot_realizations
    """
    params = []

    if symbol != "ALL":
        aliases = build_symbol_aliases(symbol)
        if aliases:
            placeholders = ",".join("?" for _ in aliases)
            sql += f" WHERE UPPER(symbol) IN ({placeholders})"
            params.extend(aliases)
        else:
            sql += " WHERE symbol = ?"
            params.append(symbol)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def get_distinct_lot_symbols(con):
    rows = con.execute(
        """
        SELECT symbol
        FROM (
            SELECT symbol FROM position_lots
            UNION
            SELECT symbol FROM lot_realizations
        )
        WHERE symbol IS NOT NULL AND symbol <> ''
        ORDER BY symbol ASC
        """
    ).fetchall()
    return [str(r["symbol"]) for r in rows]


def compute_eligible_lots_df(df_lots: pd.DataFrame, current_price):
    if df_lots.empty:
        return df_lots.copy()

    df = df_lots.copy()
    for col in ["qty_opened", "qty_remaining", "entry_price", "target_roi_pct", "leverage", "target_price"]:
        _to_float_series(df, col)

    if current_price is None or current_price <= 0:
        df["eligible_now"] = False
        return df.iloc[0:0].copy()

    def _is_eligible(row) -> bool:
        side = str(row.get("side") or "").upper()
        target_price = float(row.get("target_price") or 0.0)
        qty_remaining = float(row.get("qty_remaining") or 0.0)
        status = str(row.get("status") or "").upper()

        if status != "OPEN" or qty_remaining <= 0 or target_price <= 0:
            return False

        if side == "LONG":
            return float(current_price) >= target_price
        if side == "SHORT":
            return float(current_price) <= target_price
        return False

    df["eligible_now"] = df.apply(_is_eligible, axis=1)
    return df[df["eligible_now"]].copy()


def get_open_lots_for_symbol(con, symbol: str):
    aliases = build_symbol_aliases(symbol)
    if aliases:
        placeholders = ",".join("?" for _ in aliases)
        rows = con.execute(
            f"""
            SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
                   target_roi_pct, leverage, target_price, opened_at,
                   source_action_id, source_task_type, source_task_id, status
            FROM position_lots
            WHERE UPPER(symbol) IN ({placeholders})
              AND status = 'OPEN'
              AND COALESCE(qty_remaining, 0) > 0
            ORDER BY id ASC
            """,
            tuple(aliases),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
                   target_roi_pct, leverage, target_price, opened_at,
                   source_action_id, source_task_type, source_task_id, status
            FROM position_lots
            WHERE symbol = ?
              AND status = 'OPEN'
              AND COALESCE(qty_remaining, 0) > 0
            ORDER BY id ASC
            """,
            (symbol,),
        ).fetchall()
    return [dict(r) for r in rows]

from __future__ import annotations

import pandas as pd
from core.streamlit_services.common import _to_float_series
from core.symbol_utils import build_symbol_aliases
from core.fill_registry import (
    register_close_fill_from_action,
    register_open_fill_from_action,
)


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


def list_done_actions_for_lot_backfill(con, symbol: str = "ALL", limit: int = 100):
    sql = """
        SELECT id, created_at, symbol, panel_mode, side, qty, limit_price, leverage, api_order_id, status, note
        FROM action_queue
        WHERE status = 'DONE'
    """
    params = []

    if symbol != "ALL":
        aliases = build_symbol_aliases(symbol)
        if aliases:
            placeholders = ",".join("?" for _ in aliases)
            sql += f" AND UPPER(symbol) IN ({placeholders})"
            params.extend(aliases)
        else:
            sql += " AND symbol = ?"
            params.append(symbol)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    rows = con.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def backfill_lot_from_action_queue(
    con,
    *,
    action_id: int,
    fill_qty: float | None = None,
    fill_price: float | None = None,
    eligible_first: bool = True,
):
    row = con.execute(
        """
        SELECT id, created_at, symbol, panel_mode, side, qty, limit_price, leverage, api_order_id, status, note
        FROM action_queue
        WHERE id = ?
        LIMIT 1
        """,
        (int(action_id),),
    ).fetchone()

    if row is None:
        raise ValueError(f"Action not found: {int(action_id)}")

    action = dict(row)
    if str(action.get("status") or "").upper() != "DONE":
        raise ValueError(f"Action {int(action_id)} is not DONE")

    qty_value = float(fill_qty) if fill_qty is not None else float(action.get("qty") or 0.0)
    price_value = float(fill_price) if fill_price is not None else float(action.get("limit_price") or 0.0)

    if qty_value <= 0:
        raise ValueError(f"Invalid fill qty for action {int(action_id)}")
    if price_value <= 0:
        raise ValueError(f"Invalid fill price for action {int(action_id)}")

    panel_mode = str(action.get("panel_mode") or "OPEN").upper()

    if panel_mode == "OPEN":
        result = register_open_fill_from_action(
            con,
            action_id=int(action["id"]),
            symbol=str(action["symbol"]),
            side=str(action["side"]),
            qty_opened=qty_value,
            entry_price=price_value,
            target_roi_pct=200.0,
            leverage=float(action.get("leverage") or 500.0),
            opened_at=action.get("created_at"),
            source_task_type="ACTION_QUEUE",
            source_task_id=int(action["id"]),
        )
    else:
        result = register_close_fill_from_action(
            con,
            action_id=int(action["id"]),
            symbol=str(action["symbol"]),
            side=str(action["side"]),
            close_qty=qty_value,
            close_price=price_value,
            closed_at=action.get("created_at"),
            close_task_type="ACTION_QUEUE",
            close_task_id=int(action["id"]),
            note=f"manual backfill from action_queue id={int(action['id'])}",
            eligible_first=bool(eligible_first),
        )

    return {
        "action": action,
        "panel_mode": panel_mode,
        "fill_qty": qty_value,
        "fill_price": price_value,
        "result": result,
    }

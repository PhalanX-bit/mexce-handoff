# core/fill_registry.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.lots import (
    close_lots_for_qty,
    create_position_lot,
)
from core.symbol_utils import canonical_futures_symbol


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def _norm_symbol(symbol: Optional[str]) -> str:
    canonical = canonical_futures_symbol(symbol)
    if canonical:
        return canonical
    return str(symbol or "").strip().upper()


def _norm_side(side: Optional[str]) -> str:
    return str(side or "").strip().upper()


def _open_lot_already_registered(
    con,
    *,
    action_id: Optional[int],
    symbol: str,
    side: str,
    source_task_type: Optional[str],
    source_task_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    action_id_i = _safe_int(action_id)
    task_id_i = _safe_int(source_task_id)
    symbol_n = _norm_symbol(symbol)
    side_n = _norm_side(side)
    task_type_s = str(source_task_type or "").strip()

    row = None

    if action_id_i is not None:
        row = con.execute(
            """
            SELECT id, symbol, side, qty_opened, qty_remaining, entry_price, target_price,
                   opened_at, source_action_id, source_task_type, source_task_id, status
            FROM position_lots
            WHERE source_action_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (action_id_i,),
        ).fetchone()
        if row:
            return dict(row)

    if task_type_s and task_id_i is not None:
        row = con.execute(
            """
            SELECT id, symbol, side, qty_opened, qty_remaining, entry_price, target_price,
                   opened_at, source_action_id, source_task_type, source_task_id, status
            FROM position_lots
            WHERE UPPER(symbol) = ?
              AND UPPER(side) = ?
              AND source_task_type = ?
              AND source_task_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol_n, side_n, task_type_s, task_id_i),
        ).fetchone()
        if row:
            return dict(row)

    return None


def _close_realization_already_registered(
    con,
    *,
    close_action_id: Optional[int],
    symbol: str,
    side: str,
    close_task_type: Optional[str],
    close_task_id: Optional[int],
) -> List[Dict[str, Any]]:
    action_id_i = _safe_int(close_action_id)
    task_id_i = _safe_int(close_task_id)
    symbol_n = _norm_symbol(symbol)
    side_n = _norm_side(side)
    task_type_s = str(close_task_type or "").strip()

    if action_id_i is not None:
        rows = con.execute(
            """
            SELECT id, lot_id, symbol, side, close_qty, entry_price, close_price,
                   target_price, realized_roi_pct, closed_at,
                   close_action_id, close_task_type, close_task_id, note
            FROM lot_realizations
            WHERE close_action_id = ?
            ORDER BY id ASC
            """,
            (action_id_i,),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]

    if task_type_s and task_id_i is not None:
        rows = con.execute(
            """
            SELECT id, lot_id, symbol, side, close_qty, entry_price, close_price,
                   target_price, realized_roi_pct, closed_at,
                   close_action_id, close_task_type, close_task_id, note
            FROM lot_realizations
            WHERE UPPER(symbol) = ?
              AND UPPER(side) = ?
              AND close_task_type = ?
              AND close_task_id = ?
            ORDER BY id ASC
            """,
            (symbol_n, side_n, task_type_s, task_id_i),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]

    return []


def register_open_fill_from_action(
    con,
    *,
    action_id: Optional[int],
    symbol: str,
    side: str,
    qty_opened: float,
    entry_price: float,
    target_roi_pct: float = 200.0,
    leverage: float = 500.0,
    opened_at: Optional[str] = None,
    source_task_type: Optional[str] = "ACTION_QUEUE",
    source_task_id: Optional[int] = None,
) -> Dict[str, Any]:
    symbol_n = _norm_symbol(symbol)
    side_n = _norm_side(side)
    qty_opened_f = _safe_float(qty_opened, 0.0)
    entry_price_f = _safe_float(entry_price, 0.0)
    target_roi_pct_f = _safe_float(target_roi_pct, 200.0)
    leverage_f = _safe_float(leverage, 500.0)
    action_id_i = _safe_int(action_id)
    source_task_id_i = _safe_int(source_task_id)

    if not symbol_n:
        raise ValueError("symbol is required")
    if side_n not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if qty_opened_f <= 0:
        raise ValueError("qty_opened must be > 0")
    if entry_price_f <= 0:
        raise ValueError("entry_price must be > 0")

    existing = _open_lot_already_registered(
        con,
        action_id=action_id_i,
        symbol=symbol_n,
        side=side_n,
        source_task_type=source_task_type,
        source_task_id=source_task_id_i,
    )
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "lot_id": int(existing["id"]),
            "lot": existing,
        }

    lot_id = create_position_lot(
        con,
        symbol=symbol_n,
        side=side_n,
        qty_opened=qty_opened_f,
        entry_price=entry_price_f,
        target_roi_pct=target_roi_pct_f,
        leverage=leverage_f,
        opened_at=opened_at,
        source_action_id=action_id_i,
        source_task_type=source_task_type,
        source_task_id=source_task_id_i,
    )

    row = con.execute(
        """
        SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
               target_roi_pct, leverage, target_price, opened_at,
               source_action_id, source_task_type, source_task_id, status
        FROM position_lots
        WHERE id = ?
        LIMIT 1
        """,
        (lot_id,),
    ).fetchone()

    return {
        "ok": True,
        "duplicate": False,
        "lot_id": int(lot_id),
        "lot": dict(row) if row else None,
    }


def register_close_fill_from_action(
    con,
    *,
    action_id: Optional[int],
    symbol: str,
    side: str,
    close_qty: float,
    close_price: float,
    closed_at: Optional[str] = None,
    close_task_type: Optional[str] = "ACTION_QUEUE",
    close_task_id: Optional[int] = None,
    note: Optional[str] = None,
    eligible_first: bool = True,
) -> Dict[str, Any]:
    symbol_n = _norm_symbol(symbol)
    side_n = _norm_side(side)
    close_qty_f = _safe_float(close_qty, 0.0)
    close_price_f = _safe_float(close_price, 0.0)
    action_id_i = _safe_int(action_id)
    close_task_id_i = _safe_int(close_task_id)

    if not symbol_n:
        raise ValueError("symbol is required")
    if side_n not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if close_qty_f <= 0:
        raise ValueError("close_qty must be > 0")
    if close_price_f <= 0:
        raise ValueError("close_price must be > 0")

    existing_realizations = _close_realization_already_registered(
        con,
        close_action_id=action_id_i,
        symbol=symbol_n,
        side=side_n,
        close_task_type=close_task_type,
        close_task_id=close_task_id_i,
    )
    if existing_realizations:
        matched_rows = []
        total_qty = 0.0

        for r in existing_realizations:
            matched_qty = _safe_float(r.get("close_qty"), 0.0)
            total_qty += matched_qty

            lot_row = con.execute(
                """
                SELECT id, qty_remaining, status
                FROM position_lots
                WHERE id = ?
                LIMIT 1
                """,
                (_safe_int(r.get("lot_id")),),
            ).fetchone()

            qty_remaining_after = _safe_float((dict(lot_row) if lot_row else {}).get("qty_remaining"), 0.0)

            matched_rows.append(
                {
                    "lot_id": _safe_int(r.get("lot_id")),
                    "matched_qty": matched_qty,
                    "entry_price": _safe_float(r.get("entry_price"), 0.0),
                    "target_price": _safe_float(r.get("target_price"), 0.0),
                    "close_price": _safe_float(r.get("close_price"), 0.0),
                    "realized_roi_pct": _safe_float(r.get("realized_roi_pct"), 0.0),
                    "qty_remaining_after": qty_remaining_after,
                    "was_eligible_at_close": None,
                }
            )

        return {
            "ok": True,
            "duplicate": True,
            "matched": matched_rows,
            "requested_close_qty": close_qty_f,
            "matched_close_qty": total_qty,
            "unmatched_close_qty": max(0.0, close_qty_f - total_qty),
        }

    matched = close_lots_for_qty(
        con,
        symbol=symbol_n,
        side=side_n,
        close_qty=close_qty_f,
        close_price=close_price_f,
        closed_at=closed_at,
        close_action_id=action_id_i,
        close_task_type=close_task_type,
        close_task_id=close_task_id_i,
        note=note,
        eligible_first=eligible_first,
    )

    matched_close_qty = sum(_safe_float(x.get("matched_qty"), 0.0) for x in matched)
    unmatched_close_qty = max(0.0, close_qty_f - matched_close_qty)

    return {
        "ok": True,
        "duplicate": False,
        "matched": matched,
        "requested_close_qty": close_qty_f,
        "matched_close_qty": matched_close_qty,
        "unmatched_close_qty": unmatched_close_qty,
    }

# core/lots.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def compute_target_price(
    *,
    side: str,
    entry_price: float,
    target_roi_pct: float = 200.0,
    leverage: float = 500.0,
) -> float:
    side_u = str(side).upper().strip()
    entry_price = _safe_float(entry_price, 0.0)
    target_roi_pct = _safe_float(target_roi_pct, 200.0)
    leverage = _safe_float(leverage, 500.0)

    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    if leverage <= 0:
        raise ValueError("leverage must be > 0")
    if side_u not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")

    move_frac = (target_roi_pct / leverage) / 100.0

    if side_u == "LONG":
        return entry_price * (1.0 + move_frac)
    return entry_price * (1.0 - move_frac)


def _find_existing_open_lot(
    con,
    *,
    source_action_id: Optional[int],
    source_task_type: Optional[str],
    source_task_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    if source_action_id is None and source_task_id is None and not source_task_type:
        return None

    row = con.execute(
        """
        SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
               target_roi_pct, leverage, target_price, opened_at,
               source_action_id, source_task_type, source_task_id, status
        FROM position_lots
        WHERE COALESCE(source_action_id, -1) = COALESCE(?, -1)
          AND COALESCE(source_task_type, '') = COALESCE(?, '')
          AND COALESCE(source_task_id, -1) = COALESCE(?, -1)
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            _safe_int(source_action_id),
            source_task_type,
            _safe_int(source_task_id),
        ),
    ).fetchone()

    return dict(row) if row else None


def _find_existing_realization(
    con,
    *,
    lot_id: int,
    close_action_id: Optional[int],
    close_task_type: Optional[str],
    close_task_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    if close_action_id is None and close_task_id is None and not close_task_type:
        return None

    row = con.execute(
        """
        SELECT id, lot_id, symbol, side, close_qty, entry_price, close_price,
               target_price, realized_roi_pct, closed_at,
               close_action_id, close_task_type, close_task_id, note
        FROM lot_realizations
        WHERE lot_id = ?
          AND COALESCE(close_action_id, -1) = COALESCE(?, -1)
          AND COALESCE(close_task_type, '') = COALESCE(?, '')
          AND COALESCE(close_task_id, -1) = COALESCE(?, -1)
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            int(lot_id),
            _safe_int(close_action_id),
            close_task_type,
            _safe_int(close_task_id),
        ),
    ).fetchone()

    return dict(row) if row else None


def create_position_lot(
    con,
    *,
    symbol: str,
    side: str,
    qty_opened: float,
    entry_price: float,
    target_roi_pct: float = 200.0,
    leverage: float = 500.0,
    opened_at: Optional[str] = None,
    source_action_id: Optional[int] = None,
    source_task_type: Optional[str] = None,
    source_task_id: Optional[int] = None,
) -> int:
    symbol = str(symbol).strip()
    side = str(side).upper().strip()
    qty_opened = _safe_float(qty_opened, 0.0)
    entry_price = _safe_float(entry_price, 0.0)
    target_roi_pct = _safe_float(target_roi_pct, 200.0)
    leverage = _safe_float(leverage, 500.0)
    opened_at = opened_at or now_utc_iso()

    if not symbol:
        raise ValueError("symbol is required")
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if qty_opened <= 0:
        raise ValueError("qty_opened must be > 0")
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    if leverage <= 0:
        raise ValueError("leverage must be > 0")

    existing = _find_existing_open_lot(
        con,
        source_action_id=source_action_id,
        source_task_type=source_task_type,
        source_task_id=source_task_id,
    )
    if existing:
        return int(existing["id"])

    target_price = compute_target_price(
        side=side,
        entry_price=entry_price,
        target_roi_pct=target_roi_pct,
        leverage=leverage,
    )

    cur = con.execute(
        """
        INSERT INTO position_lots (
            symbol,
            side,
            qty_opened,
            qty_remaining,
            entry_price,
            target_roi_pct,
            leverage,
            target_price,
            opened_at,
            source_action_id,
            source_task_type,
            source_task_id,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            symbol,
            side,
            qty_opened,
            qty_opened,
            entry_price,
            target_roi_pct,
            leverage,
            target_price,
            opened_at,
            _safe_int(source_action_id),
            source_task_type,
            _safe_int(source_task_id),
        ),
    )
    return int(cur.lastrowid)


def list_open_lots(
    con,
    *,
    symbol: str,
    side: str,
) -> List[Dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, symbol, side, qty_opened, qty_remaining, entry_price,
               target_roi_pct, leverage, target_price, opened_at,
               source_action_id, source_task_type, source_task_id, status
        FROM position_lots
        WHERE symbol = ?
          AND side = ?
          AND status = 'OPEN'
          AND COALESCE(qty_remaining, 0) > 0
        ORDER BY id ASC
        """,
        (symbol, side),
    ).fetchall()
    return [dict(r) for r in rows]


def is_lot_eligible_for_close(
    *,
    side: str,
    target_price: float,
    close_price: float,
) -> bool:
    side_u = str(side).upper().strip()
    target_price = _safe_float(target_price, 0.0)
    close_price = _safe_float(close_price, 0.0)

    if target_price <= 0 or close_price <= 0:
        return False

    if side_u == "LONG":
        return close_price >= target_price
    if side_u == "SHORT":
        return close_price <= target_price
    return False


def compute_realized_roi_pct(
    *,
    side: str,
    entry_price: float,
    close_price: float,
    leverage: float,
) -> float:
    side_u = str(side).upper().strip()
    entry_price = _safe_float(entry_price, 0.0)
    close_price = _safe_float(close_price, 0.0)
    leverage = _safe_float(leverage, 0.0)

    if entry_price <= 0 or close_price <= 0 or leverage <= 0:
        return 0.0

    if side_u == "LONG":
        move_frac = (close_price - entry_price) / entry_price
    elif side_u == "SHORT":
        move_frac = (entry_price - close_price) / entry_price
    else:
        return 0.0

    return move_frac * leverage * 100.0


def close_lots_for_qty(
    con,
    *,
    symbol: str,
    side: str,
    close_qty: float,
    close_price: float,
    closed_at: Optional[str] = None,
    close_action_id: Optional[int] = None,
    close_task_type: Optional[str] = None,
    close_task_id: Optional[int] = None,
    note: Optional[str] = None,
    eligible_first: bool = True,
) -> List[Dict[str, Any]]:
    symbol = str(symbol).strip()
    side = str(side).upper().strip()
    close_qty = _safe_float(close_qty, 0.0)
    close_price = _safe_float(close_price, 0.0)
    closed_at = closed_at or now_utc_iso()

    if not symbol:
        raise ValueError("symbol is required")
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if close_qty <= 0:
        raise ValueError("close_qty must be > 0")
    if close_price <= 0:
        raise ValueError("close_price must be > 0")

    lots = list_open_lots(con, symbol=symbol, side=side)

    if eligible_first:
        eligible = []
        non_eligible = []
        for lot in lots:
            if is_lot_eligible_for_close(
                side=side,
                target_price=_safe_float(lot.get("target_price"), 0.0),
                close_price=close_price,
            ):
                eligible.append(lot)
            else:
                non_eligible.append(lot)
        ordered_lots = eligible + non_eligible
    else:
        ordered_lots = lots

    qty_left = close_qty
    matched: List[Dict[str, Any]] = []

    for lot in ordered_lots:
        if qty_left <= 0:
            break

        lot_id = int(lot["id"])
        qty_remaining_before = _safe_float(lot.get("qty_remaining"), 0.0)
        if qty_remaining_before <= 0:
            continue

        existing_realization = _find_existing_realization(
            con,
            lot_id=lot_id,
            close_action_id=close_action_id,
            close_task_type=close_task_type,
            close_task_id=close_task_id,
        )
        if existing_realization:
            continue

        matched_qty = min(qty_left, qty_remaining_before)
        qty_remaining_after = qty_remaining_before - matched_qty

        entry_price = _safe_float(lot.get("entry_price"), 0.0)
        leverage = _safe_float(lot.get("leverage"), 500.0)
        target_price = _safe_float(lot.get("target_price"), 0.0)

        realized_roi_pct = compute_realized_roi_pct(
            side=side,
            entry_price=entry_price,
            close_price=close_price,
            leverage=leverage,
        )

        con.execute(
            """
            INSERT INTO lot_realizations (
                lot_id,
                symbol,
                side,
                close_qty,
                entry_price,
                close_price,
                target_price,
                realized_roi_pct,
                closed_at,
                close_action_id,
                close_task_type,
                close_task_id,
                note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lot_id,
                symbol,
                side,
                matched_qty,
                entry_price,
                close_price,
                target_price if target_price > 0 else None,
                realized_roi_pct,
                closed_at,
                _safe_int(close_action_id),
                close_task_type,
                _safe_int(close_task_id),
                note,
            ),
        )

        if qty_remaining_after <= 0:
            con.execute(
                """
                UPDATE position_lots
                SET qty_remaining = 0,
                    status = 'CLOSED'
                WHERE id = ?
                """,
                (lot_id,),
            )
        else:
            con.execute(
                """
                UPDATE position_lots
                SET qty_remaining = ?,
                    status = 'OPEN'
                WHERE id = ?
                """,
                (qty_remaining_after, lot_id),
            )

        was_eligible = is_lot_eligible_for_close(
            side=side,
            target_price=target_price,
            close_price=close_price,
        )

        matched.append(
            {
                "lot_id": lot_id,
                "matched_qty": matched_qty,
                "entry_price": entry_price,
                "target_price": target_price,
                "close_price": close_price,
                "realized_roi_pct": realized_roi_pct,
                "qty_remaining_after": qty_remaining_after,
                "was_eligible_at_close": was_eligible,
            }
        )

        qty_left -= matched_qty

    return matched


# ---------- compatibility wrappers ----------

def create_lot_from_open_fill(
    con,
    *,
    symbol: str,
    side: str,
    qty: float,
    entry_price: float,
    target_roi_pct: float = 200.0,
    leverage: float = 500.0,
    opened_at: Optional[str] = None,
    source_action_id: Optional[int] = None,
    source_task_type: Optional[str] = None,
    source_task_id: Optional[int] = None,
) -> int:
    return create_position_lot(
        con,
        symbol=symbol,
        side=side,
        qty_opened=qty,
        entry_price=entry_price,
        target_roi_pct=target_roi_pct,
        leverage=leverage,
        opened_at=opened_at,
        source_action_id=source_action_id,
        source_task_type=source_task_type,
        source_task_id=source_task_id,
    )


def close_qty_against_lots(
    con,
    *,
    symbol: str,
    side: str,
    close_qty: float,
    close_price: float,
    closed_at: Optional[str] = None,
    close_action_id: Optional[int] = None,
    close_task_type: Optional[str] = None,
    close_task_id: Optional[int] = None,
    note: Optional[str] = None,
    eligible_first: bool = True,
) -> List[Dict[str, Any]]:
    return close_lots_for_qty(
        con,
        symbol=symbol,
        side=side,
        close_qty=close_qty,
        close_price=close_price,
        closed_at=closed_at,
        close_action_id=close_action_id,
        close_task_type=close_task_type,
        close_task_id=close_task_id,
        note=note,
        eligible_first=eligible_first,
    )
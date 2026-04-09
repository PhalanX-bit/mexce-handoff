from __future__ import annotations

from typing import Any, Optional

from core.symbol_utils import canonical_futures_symbol


def add_action_ledger(con, created_at, symbol, action_type, side=None, qty=None, price=None, note=None):
    con.execute(
        """
        INSERT INTO actions_ledger (created_at, symbol, action_type, side, qty, price, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, symbol, action_type, side, qty, price, note),
    )


def build_action_queue_ledger_note(action_id: int, status: str, extra: Optional[str] = None) -> str:
    base = f"action_id={int(action_id)} | status={str(status).upper()}"
    if extra:
        return f"{base} | {str(extra).strip()}"
    return base


def log_action_queue_event(
    con,
    *,
    created_at: str,
    action_id: int,
    symbol: str,
    event_type: str,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    note: Optional[str] = None,
) -> bool:
    canonical_symbol = canonical_futures_symbol(symbol) or str(symbol or "").strip().upper()
    event_type = str(event_type or "").strip().upper()
    note_text = str(note or "").strip() or None
    event_marker = f"action_id={int(action_id)}"

    existing = con.execute(
        """
        SELECT id
        FROM actions_ledger
        WHERE action_type = ?
          AND symbol = ?
          AND note LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            event_type,
            canonical_symbol,
            f"%{event_marker}%",
        ),
    ).fetchone()
    if existing is not None:
        return False

    add_action_ledger(
        con,
        created_at=created_at,
        symbol=canonical_symbol,
        action_type=event_type,
        side=(str(side).strip().upper() if side else None),
        qty=float(qty) if qty is not None else None,
        price=float(price) if price is not None else None,
        note=note_text,
    )
    return True


def get_recent_actions(con, limit=50):
    rows = con.execute(
        """
        SELECT id, created_at, symbol, action_type, side, qty, price, note
        FROM actions_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

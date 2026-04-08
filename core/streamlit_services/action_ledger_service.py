from __future__ import annotations


def add_action_ledger(con, created_at, symbol, action_type, side=None, qty=None, price=None, note=None):
    con.execute(
        """
        INSERT INTO actions_ledger (created_at, symbol, action_type, side, qty, price, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, symbol, action_type, side, qty, price, note),
    )


def get_recent_actions(con, limit=50):
    rows = con.execute(
        """
        SELECT created_at, symbol, action_type, side, qty, price, note
        FROM actions_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
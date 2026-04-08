from __future__ import annotations


def get_pending_chase_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pending_chase_tasks
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "FAILED": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def list_pending_chase_tasks(con, status: str = "ALL", limit: int = 200):
    if status == "ALL":
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, qty, status,
                   baseline_contracts, filled_contracts,
                   created_at, updated_at, resolved_at, note
            FROM pending_chase_tasks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, qty, status,
                   baseline_contracts, filled_contracts,
                   created_at, updated_at, resolved_at, note
            FROM pending_chase_tasks
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return rows


def reset_pending_chase_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET status='PENDING',
            baseline_contracts=NULL,
            filled_contracts=NULL,
            resolved_at=NULL,
            updated_at=datetime('now'),
            created_at=datetime('now'),
            note='RESET_FOR_TEST'
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def set_pending_chase_created_now(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET created_at=datetime('now'),
            updated_at=datetime('now')
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def delete_resolved_pending_chase_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_chase_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED')
        """
    )
    con.commit()
    return cur.rowcount


def delete_pending_chase_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_chase_tasks
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def mark_pending_chase_task_status(con, task_id: int, new_status: str, note_suffix: str = "") -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET status=?,
            updated_at=datetime('now'),
            resolved_at=CASE
                WHEN ? IN ('FILLED', 'EXPIRED', 'FAILED')
                THEN datetime('now')
                ELSE resolved_at
            END,
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (new_status, new_status, note_suffix, task_id),
    )
    con.commit()
    return cur.rowcount
from __future__ import annotations

from core.symbol_utils import canonical_futures_symbol


def get_pending_limit_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pending_limit_tasks
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "TRIGGERED": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "FAILED_AFTER_TRIGGER": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def get_pending_limit_audit_counts(con):
    row = con.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN aq.id IS NULL THEN 1 ELSE 0 END) AS orphan_rows,
            SUM(
                CASE
                    WHEN aq.id IS NULL
                     AND plt.status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
                    THEN 1
                    ELSE 0
                END
            ) AS orphan_resolved_rows,
            SUM(
                CASE
                    WHEN aq.id IS NULL
                     AND plt.status = 'FAILED_AFTER_TRIGGER'
                    THEN 1
                    ELSE 0
                END
            ) AS orphan_failed_after_trigger_rows
        FROM pending_limit_tasks plt
        LEFT JOIN action_queue aq
          ON aq.id = plt.action_id
        """
    ).fetchone()

    if row is None:
        return {
            "total_rows": 0,
            "orphan_rows": 0,
            "orphan_resolved_rows": 0,
            "orphan_failed_after_trigger_rows": 0,
        }

    return {
        "total_rows": int(row["total_rows"] or 0),
        "orphan_rows": int(row["orphan_rows"] or 0),
        "orphan_resolved_rows": int(row["orphan_resolved_rows"] or 0),
        "orphan_failed_after_trigger_rows": int(row["orphan_failed_after_trigger_rows"] or 0),
    }


def list_pending_limit_tasks(con, status: str = "ALL", limit: int = 200):
    if status == "ALL":
        rows = con.execute(
            """
            SELECT plt.id, plt.action_id, plt.symbol, plt.side, plt.panel_mode, plt.limit_price, plt.qty, plt.status,
                   plt.trigger_seen, plt.baseline_contracts, plt.triggered_at,
                   plt.attempt_count, plt.created_at, plt.updated_at, plt.resolved_at, plt.note,
                   aq.status AS action_status,
                   CASE WHEN aq.id IS NULL THEN 0 ELSE 1 END AS action_exists
            FROM pending_limit_tasks plt
            LEFT JOIN action_queue aq
              ON aq.id = plt.action_id
            ORDER BY plt.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT plt.id, plt.action_id, plt.symbol, plt.side, plt.panel_mode, plt.limit_price, plt.qty, plt.status,
                   plt.trigger_seen, plt.baseline_contracts, plt.triggered_at,
                   plt.attempt_count, plt.created_at, plt.updated_at, plt.resolved_at, plt.note,
                   aq.status AS action_status,
                   CASE WHEN aq.id IS NULL THEN 0 ELSE 1 END AS action_exists
            FROM pending_limit_tasks plt
            LEFT JOIN action_queue aq
              ON aq.id = plt.action_id
            WHERE plt.status = ?
            ORDER BY plt.id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return rows


def reset_pending_limit_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET status='PENDING',
            trigger_seen=0,
            baseline_contracts=NULL,
            triggered_at=NULL,
            resolved_at=NULL,
            attempt_count=0,
            updated_at=datetime('now'),
            created_at=datetime('now'),
            note='RESET_FOR_TEST'
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def set_pending_limit_created_now(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET created_at=datetime('now'),
            updated_at=datetime('now')
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def delete_resolved_pending_limit_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
        """
    )
    con.commit()
    return cur.rowcount


def delete_orphaned_resolved_pending_limit_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
          AND NOT EXISTS (
              SELECT 1
              FROM action_queue aq
              WHERE aq.id = pending_limit_tasks.action_id
          )
        """
    )
    con.commit()
    return cur.rowcount


def delete_pending_limit_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def mark_pending_limit_task_status(con, task_id: int, new_status: str, note_suffix: str = "") -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET status=?,
            updated_at=datetime('now'),
            resolved_at=CASE
                WHEN ? IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
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


def normalize_pending_limit_task_symbols(con) -> int:
    rows = con.execute(
        """
        SELECT id, symbol, note
        FROM pending_limit_tasks
        ORDER BY id ASC
        """
    ).fetchall()

    updated = 0
    for row in rows:
        current_symbol = str(row["symbol"] or "").strip()
        normalized_symbol = canonical_futures_symbol(current_symbol)
        if not normalized_symbol:
            continue
        if normalized_symbol == current_symbol.upper():
            continue

        con.execute(
            """
            UPDATE pending_limit_tasks
            SET symbol = ?,
                updated_at = datetime('now'),
                note = COALESCE(note, '') || ?
            WHERE id = ?
            """,
            (
                normalized_symbol,
                f" | symbol_normalized_from={current_symbol}",
                int(row["id"]),
            ),
        )
        updated += 1

    con.commit()
    return updated

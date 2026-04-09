from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "mexc.sqlite"


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def count_rows(con, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0] or 0) if row else 0


def main() -> None:
    apply = "--apply" in sys.argv[1:]

    con = connect()
    try:
        orphan_resolved_limit = count_rows(
            con,
            """
            SELECT COUNT(*)
            FROM pending_limit_tasks
            WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_queue aq
                  WHERE aq.id = pending_limit_tasks.action_id
              )
            """,
        )
        resolved_chase = count_rows(
            con,
            """
            SELECT COUNT(*)
            FROM pending_chase_tasks
            WHERE status IN ('FILLED', 'EXPIRED', 'FAILED')
            """,
        )

        summary = {
            "db": str(DB),
            "mode": "apply" if apply else "dry_run",
            "delete_orphan_resolved_pending_limit_tasks": int(orphan_resolved_limit),
            "delete_resolved_pending_chase_tasks": int(resolved_chase),
        }

        if not apply:
            print(summary)
            return

        deleted_limit = con.execute(
            """
            DELETE FROM pending_limit_tasks
            WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
              AND NOT EXISTS (
                  SELECT 1
                  FROM action_queue aq
                  WHERE aq.id = pending_limit_tasks.action_id
              )
            """
        ).rowcount
        deleted_chase = con.execute(
            """
            DELETE FROM pending_chase_tasks
            WHERE status IN ('FILLED', 'EXPIRED', 'FAILED')
            """
        ).rowcount
        con.commit()

        print(
            {
                **summary,
                "deleted_orphan_resolved_pending_limit_tasks": int(deleted_limit),
                "deleted_resolved_pending_chase_tasks": int(deleted_chase),
            }
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()

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


def fetch_ids(con, sql: str, params: tuple = ()) -> list[int]:
    rows = con.execute(sql, params).fetchall()
    return [int(r[0]) for r in rows]


def preview_rows(con, sql: str, params: tuple = ()) -> list[dict]:
    rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete_by_ids(con, table: str, ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = con.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", tuple(ids))
    return int(cur.rowcount or 0)


def main() -> None:
    apply = "--apply" in sys.argv[1:]

    con = connect()
    try:
        smoke_action_ids = fetch_ids(
            con,
            """
            SELECT id
            FROM action_queue
            WHERE COALESCE(note, '') LIKE '%smoke%'
               OR COALESCE(note, '') LIKE '%test%'
               OR COALESCE(created_by, '') LIKE 'test_%'
            ORDER BY id ASC
            """,
        )

        smoke_lot_ids = fetch_ids(
            con,
            """
            SELECT id
            FROM position_lots
            WHERE COALESCE(source_task_type, '') LIKE 'TEST%'
               OR COALESCE(symbol, '') LIKE 'TEST%'
            ORDER BY id ASC
            """,
        )

        smoke_realization_ids = fetch_ids(
            con,
            """
            SELECT id
            FROM lot_realizations
            WHERE COALESCE(note, '') LIKE '%test%'
               OR COALESCE(symbol, '') LIKE 'TEST%'
               OR COALESCE(close_task_type, '') LIKE 'TEST%'
            ORDER BY id ASC
            """,
        )

        smoke_action_rows = preview_rows(
            con,
            """
            SELECT id, created_at, created_by, symbol, status, note
            FROM action_queue
            WHERE id IN ({})
            ORDER BY id ASC
            """.format(",".join("?" for _ in smoke_action_ids) if smoke_action_ids else "NULL"),
            tuple(smoke_action_ids),
        ) if smoke_action_ids else []

        smoke_lot_rows = preview_rows(
            con,
            """
            SELECT id, symbol, source_action_id, source_task_type, source_task_id, status
            FROM position_lots
            WHERE id IN ({})
            ORDER BY id ASC
            """.format(",".join("?" for _ in smoke_lot_ids) if smoke_lot_ids else "NULL"),
            tuple(smoke_lot_ids),
        ) if smoke_lot_ids else []

        smoke_realization_rows = preview_rows(
            con,
            """
            SELECT id, symbol, close_action_id, close_task_type, close_task_id, note
            FROM lot_realizations
            WHERE id IN ({})
            ORDER BY id ASC
            """.format(",".join("?" for _ in smoke_realization_ids) if smoke_realization_ids else "NULL"),
            tuple(smoke_realization_ids),
        ) if smoke_realization_ids else []

        ledger_smoke_ids = fetch_ids(
            con,
            """
            SELECT id
            FROM actions_ledger
            WHERE COALESCE(note, '') LIKE '%smoke%'
               OR COALESCE(note, '') LIKE '%test%'
               OR COALESCE(note, '') LIKE '%action_id=256%'
               OR COALESCE(note, '') LIKE '%action_id=257%'
               OR COALESCE(note, '') LIKE '%action_id=258%'
            ORDER BY id ASC
            """,
        )

        ledger_rows = preview_rows(
            con,
            """
            SELECT id, created_at, symbol, action_type, note
            FROM actions_ledger
            WHERE id IN ({})
            ORDER BY id ASC
            """.format(",".join("?" for _ in ledger_smoke_ids) if ledger_smoke_ids else "NULL"),
            tuple(ledger_smoke_ids),
        ) if ledger_smoke_ids else []

        summary = {
            "db": str(DB),
            "mode": "apply" if apply else "dry_run",
            "delete_action_queue_rows": int(len(smoke_action_ids)),
            "delete_actions_ledger_rows": int(len(ledger_smoke_ids)),
            "delete_position_lots_rows": int(len(smoke_lot_ids)),
            "delete_lot_realizations_rows": int(len(smoke_realization_ids)),
            "action_queue_preview": smoke_action_rows[:20],
            "actions_ledger_preview": ledger_rows[:20],
            "position_lots_preview": smoke_lot_rows[:20],
            "lot_realizations_preview": smoke_realization_rows[:20],
        }

        if not apply:
            print(summary)
            return

        deleted_ledger = delete_by_ids(con, "actions_ledger", ledger_smoke_ids)
        deleted_realizations = delete_by_ids(con, "lot_realizations", smoke_realization_ids)
        deleted_lots = delete_by_ids(con, "position_lots", smoke_lot_ids)
        deleted_actions = delete_by_ids(con, "action_queue", smoke_action_ids)
        con.commit()

        print(
            {
                **summary,
                "deleted_action_queue_rows": deleted_actions,
                "deleted_actions_ledger_rows": deleted_ledger,
                "deleted_position_lots_rows": deleted_lots,
                "deleted_lot_realizations_rows": deleted_realizations,
            }
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()

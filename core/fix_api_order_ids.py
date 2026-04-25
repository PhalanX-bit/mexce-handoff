from __future__ import annotations

import sqlite3
from pathlib import Path

from core.db import DB_PATH
from core.reprice_service import normalize_api_order_id


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, api_order_id
            FROM action_queue
            WHERE api_order_id IS NOT NULL
            """
        ).fetchall()

        updated = 0
        skipped = 0

        for row in rows:
            action_id = int(row["id"])
            raw_value = row["api_order_id"]
            normalized = normalize_api_order_id(raw_value)

            if not normalized:
                skipped += 1
                continue

            if str(raw_value) == normalized:
                skipped += 1
                continue

            conn.execute(
                """
                UPDATE action_queue
                SET api_order_id = ?
                WHERE id = ?
                """,
                (normalized, action_id),
            )
            updated += 1

        conn.commit()

        print(
            {
                "db_path": str(Path(DB_PATH).resolve()),
                "updated": updated,
                "skipped": skipped,
            }
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
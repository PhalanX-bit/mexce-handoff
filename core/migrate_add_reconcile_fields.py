from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db import DB_PATH


NEW_COLUMNS = [
    ("reconcile_state", "TEXT"),
    ("reconcile_checked_at", "TEXT"),
    ("reconcile_reason", "TEXT"),
    ("reconcile_payload", "TEXT"),
]


def get_existing_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
    return {row[1] for row in rows}


def main() -> None:
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        existing = get_existing_columns(conn)

        for col_name, col_type in NEW_COLUMNS:
            if col_name in existing:
                print(f"[skip] column already exists: {col_name}")
                continue

            sql = f"ALTER TABLE action_queue ADD COLUMN {col_name} {col_type}"
            conn.execute(sql)
            print(f"[ok] added column: {col_name} {col_type}")

        conn.commit()

        print("\n=== final schema ===")
        rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
        for row in rows:
            print(
                {
                    "cid": row[0],
                    "name": row[1],
                    "type": row[2],
                    "notnull": row[3],
                    "default": row[4],
                    "pk": row[5],
                }
            )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
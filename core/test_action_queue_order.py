from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.action_queue_order import ACTION_QUEUE_EXECUTOR_ORDER_BY


def fetch_ids_in_executor_order(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        f"""
        SELECT id
        FROM action_queue
        WHERE status = 'ARMED'
        ORDER BY {ACTION_QUEUE_EXECUTOR_ORDER_BY}
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def main() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE action_queue (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO action_queue (id, status, priority) VALUES (?, ?, ?)",
            [
                (101, "ARMED", 50),
                (102, "ARMED", 10),
                (103, "ARMED", 10),
                (104, "DONE", 0),
                (105, "ARMED", 100),
            ],
        )

        ids = fetch_ids_in_executor_order(conn)
        assert ids == [102, 103, 101, 105], ids
        print("OK: executor order uses lower priority first, then lower id.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

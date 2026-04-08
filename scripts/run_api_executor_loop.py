from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db import DB_PATH
from core.api_executor import process_one_action


POLL_INTERVAL_SEC = 2.0


def fetch_next_armed_action_id() -> int | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id
            FROM action_queue
            WHERE status = 'ARMED'
            ORDER BY priority DESC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])
    finally:
        conn.close()


def main() -> None:
    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {DB_PATH}")
    print(f"DB exists = {Path(DB_PATH).exists()}")
    print(f"Starting API executor loop. poll_interval_sec={POLL_INTERVAL_SEC}")

    while True:
        action_id = fetch_next_armed_action_id()

        if action_id is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        print(f"\nProcessing action_id={action_id}")
        try:
            process_one_action(action_id=action_id, verbose=True)
        except Exception as exc:
            print(f"Unhandled executor loop error for action_id={action_id}: {type(exc).__name__}: {exc}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
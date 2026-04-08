from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.db import DB_PATH
from core.api_executor import process_one_action


def main() -> None:
    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {DB_PATH}")
    print(f"DB exists = {Path(DB_PATH).exists()}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, symbol, status, priority, created_at, panel_mode, side, qty, limit_price, order_kind
            FROM action_queue
            WHERE status = 'ARMED'
            ORDER BY priority DESC, id ASC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            print("No ARMED actions found.")
            return

        action_id = int(row["id"])
        print(
            {
                "selected_action_id": action_id,
                "symbol": row["symbol"],
                "status": row["status"],
                "priority": row["priority"],
                "created_at": row["created_at"],
                "panel_mode": row["panel_mode"],
                "side": row["side"],
                "qty": row["qty"],
                "limit_price": row["limit_price"],
                "order_kind": row["order_kind"],
            }
        )
    finally:
        conn.close()

    result = process_one_action(action_id=action_id, verbose=True)
    print("\nFINAL RESULT:")
    print(result)


if __name__ == "__main__":
    main()
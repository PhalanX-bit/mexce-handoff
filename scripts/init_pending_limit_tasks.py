import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "mexc.sqlite"


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_limit_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                limit_price REAL NOT NULL,
                qty REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                trigger_seen INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at TEXT,
                note TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pending_limit_tasks_status
            ON pending_limit_tasks(status);

            CREATE INDEX IF NOT EXISTS idx_pending_limit_tasks_symbol_status
            ON pending_limit_tasks(symbol, status);
            """
        )
        con.commit()
        print(f"OK: pending_limit_tasks ensured in {DB_PATH}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
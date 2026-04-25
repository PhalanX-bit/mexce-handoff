from __future__ import annotations

import sqlite3
from typing import Iterable


REQUIRED_COLUMNS = (
    ("ops_lock_token", "TEXT"),
    ("ops_locked_at", "TEXT"),
    ("last_reprice_payload", "TEXT"),
)


def _get_existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def ensure_action_queue_lean_hardening_columns(
    conn: sqlite3.Connection,
    table_name: str = "action_queue",
) -> list[str]:
    """
    Adds the minimal columns needed for lean hardening if they do not exist.

    Added columns:
      - ops_lock_token TEXT
      - ops_locked_at TEXT
      - last_reprice_payload TEXT
    """
    existing = _get_existing_columns(conn, table_name)
    added: list[str] = []

    for column_name, column_type in REQUIRED_COLUMNS:
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )
            added.append(column_name)

    conn.commit()
    return added


def ensure_action_queue_lean_hardening_columns_at_path(
    db_path: str,
    table_name: str = "action_queue",
) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return ensure_action_queue_lean_hardening_columns(conn, table_name=table_name)
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Ensure minimal lean-hardening columns exist in action_queue."
    )
    parser.add_argument("--db", required=True, help="Path to sqlite database")
    parser.add_argument(
        "--table",
        default="action_queue",
        help="Table name (default: action_queue)",
    )
    args = parser.parse_args()

    added_cols = ensure_action_queue_lean_hardening_columns_at_path(
        db_path=args.db,
        table_name=args.table,
    )
    print({"added_columns": added_cols})
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint

# Добавяме project root-а в sys.path, за да работят import-ите при директно пускане.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.api_executor import process_one_action
from core.db import DB_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def enqueue_test_action() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        now = utc_now_iso()

        sql = """
        INSERT INTO action_queue (
            created_at,
            created_by,
            exchange,
            market_type,
            symbol,
            intent,
            qty,
            status,
            panel_mode,
            side,
            limit_price,
            order_kind,
            order_type,
            leverage,
            qty_unit,
            trigger_type,
            reduce_only,
            note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        values = (
            now,                                # created_at
            "test_api_executor_roundtrip",      # created_by
            "MEXC",                             # exchange
            "swap",                             # market_type
            "BTC/USDT:USDT",                    # symbol
            "",                                 # intent
            1.0,                                # qty
            "ARMED",                            # status
            "OPEN",                             # panel_mode
            "SHORT",                            # side
            80500.0,                            # limit_price
            "LIMIT",                            # order_kind
            "limit",                            # order_type
            500,                                # leverage
            "contracts",                        # qty_unit
            "manual",                           # trigger_type
            0,                                  # reduce_only
            "API-only roundtrip test",          # note
        )

        cur = conn.execute(sql, values)
        action_id = cur.lastrowid
        conn.commit()
        return int(action_id)
    finally:
        conn.close()


def fetch_action(action_id: int):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT *
            FROM action_queue
            WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    action_id = enqueue_test_action()
    print(f"Enqueued action id = {action_id}")

    result = process_one_action(action_id=action_id, verbose=True)

    print("\n=== process_one_action returned ===")
    pprint(result)

    print("\n=== final row from DB ===")
    final_row = fetch_action(action_id)
    pprint(final_row)
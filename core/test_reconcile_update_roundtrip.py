from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.api_executor import process_one_action
from core.db import DB_PATH
from core.reconcile_service import reconcile_action


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def enqueue_test_action(
    *,
    created_by: str,
    limit_price: float,
    note: str,
) -> int:
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
            now,
            created_by,
            "MEXC",
            "swap",
            "BTC/USDT:USDT",
            "",
            1.0,
            "ARMED",
            "OPEN",
            "SHORT",
            float(limit_price),
            "LIMIT",
            "limit",
            500,
            "contracts",
            "manual",
            0,
            note,
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

    action_id = enqueue_test_action(
        created_by="test_reconcile_update_roundtrip",
        limit_price=80500.0,
        note="Reconcile update roundtrip test",
    )
    print(f"action_id = {action_id}")

    submit_result = process_one_action(action_id=action_id, verbose=True)

    print("\n=== submit_result ===")
    pprint(submit_result)

    row_before = fetch_action(action_id)
    print("\n=== row_before_reconcile ===")
    pprint(row_before)

    print("\n=== reconcile_action(update_action_queue=True) ===")
    reconcile_result = reconcile_action(
        action_id=action_id,
        update_action_queue=True,
    )
    pprint(reconcile_result)

    row_after = fetch_action(action_id)
    print("\n=== row_after_reconcile ===")
    pprint(row_after)

    print("\n=== extracted reconcile fields ===")
    print(f"reconcile_state      = {row_after.get('reconcile_state')}")
    print(f"reconcile_checked_at = {row_after.get('reconcile_checked_at')}")
    print(f"reconcile_reason     = {row_after.get('reconcile_reason')}")
    print(f"reconcile_payload    = {row_after.get('reconcile_payload')}")
    print(f"status               = {row_after.get('status')}")
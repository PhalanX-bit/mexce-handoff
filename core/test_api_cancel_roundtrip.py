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
from core.mexc_direct import cancel_order_raw, list_open_orders_raw


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
            now,
            "test_api_cancel_roundtrip",
            "MEXC",
            "swap",
            "BTC/USDT:USDT",
            "",
            1.0,
            "ARMED",
            "OPEN",
            "SHORT",
            80500.0,
            "LIMIT",
            "limit",
            500,
            "contracts",
            "manual",
            0,
            "API-only cancel roundtrip test",
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


def order_id_in_open_orders(payload, order_id: str) -> bool:
    oid = str(order_id)

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        data = payload.get("data")
        items = data if isinstance(data, list) else []
    else:
        items = []

    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("orderId", "order_id", "id"):
            if key in item and str(item[key]) == oid:
                return True
    return False


if __name__ == "__main__":
    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    action_id = enqueue_test_action()
    print(f"Enqueued action id = {action_id}")

    result = process_one_action(action_id=action_id, verbose=True)

    print("\n=== process_one_action returned ===")
    pprint(result)

    final_row = fetch_action(action_id)
    print("\n=== final row from DB ===")
    pprint(final_row)

    if not final_row:
        raise RuntimeError("No DB row found after execution.")

    if final_row.get("status") != "DONE":
        raise RuntimeError(f"Action did not finish as DONE. status={final_row.get('status')}")

    api_order_id = final_row.get("api_order_id")
    if api_order_id is None:
        raise RuntimeError("api_order_id is missing after successful execution.")

    api_order_id = str(api_order_id)
    print(f"\napi_order_id = {api_order_id}")

    print("\n=== open orders BEFORE cancel ===")
    open_before = list_open_orders_raw("BTC/USDT:USDT")
    pprint(open_before)
    print(f"present_before_cancel = {order_id_in_open_orders(open_before, api_order_id)}")

    print("\n=== cancel response ===")
    cancel_resp = cancel_order_raw(api_order_id)
    pprint(cancel_resp)

    print("\n=== open orders AFTER cancel ===")
    open_after = list_open_orders_raw("BTC/USDT:USDT")
    pprint(open_after)
    print(f"present_after_cancel = {order_id_in_open_orders(open_after, api_order_id)}")
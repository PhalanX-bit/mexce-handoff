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
from core.mexc_direct import list_open_orders_raw
from core.order_manager import replace_limit_order_from_action


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def enqueue_test_action(limit_price: float = 80500.0) -> int:
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
            "test_api_replace_roundtrip",
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
            "API-only replace roundtrip test",
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


def normalize_open_orders_payload(payload):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    return []


def find_open_order(symbol: str, order_id: str):
    items = normalize_open_orders_payload(list_open_orders_raw(symbol))
    oid = str(order_id)
    for item in items:
        for key in ("orderId", "order_id", "id"):
            if key in item and str(item[key]) == oid:
                return item
    return None


def is_order_open(symbol: str, order_id: str) -> bool:
    return find_open_order(symbol, order_id) is not None


if __name__ == "__main__":
    symbol = "BTC/USDT:USDT"
    initial_price = 80500.0
    replacement_price = 80450.0

    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    action_id = enqueue_test_action(limit_price=initial_price)
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

    old_order_id = final_row.get("api_order_id")
    if old_order_id is None:
        raise RuntimeError("api_order_id is missing after successful execution.")

    old_order_id = str(old_order_id)
    print(f"\nold_order_id = {old_order_id}")
    print(f"old_open_before_replace = {is_order_open(symbol, old_order_id)}")

    print("\n=== replace result ===")
    replace_result = replace_limit_order_from_action(
        action_id=action_id,
        new_price=replacement_price,
        old_order_id=old_order_id,
        require_old_open=True,
        cancel_first=True,
    )
    pprint(replace_result)

    if not replace_result.get("ok"):
        raise RuntimeError(f"Replace failed: {replace_result}")

    new_order_id = str(replace_result["new_order_id"])
    print(f"\nnew_order_id = {new_order_id}")

    print("\n=== open state checks ===")
    print(f"old_open_after_replace = {is_order_open(symbol, old_order_id)}")
    print(f"new_open_after_replace = {is_order_open(symbol, new_order_id)}")

    old_item = find_open_order(symbol, old_order_id)
    new_item = find_open_order(symbol, new_order_id)

    print("\n=== old open order item ===")
    pprint(old_item)

    print("\n=== new open order item ===")
    pprint(new_item)
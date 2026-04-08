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
from core.reprice_service import RepriceServiceConfig
from core.reprice_worker import RepriceWorkerConfig, run_reprice_pass


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

    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    # 1) Създаваме 2 action-а
    action_id_a = enqueue_test_action(
        created_by="test_reprice_worker_roundtrip_A",
        limit_price=80500.0,
        note="Worker roundtrip target action",
    )
    action_id_b = enqueue_test_action(
        created_by="test_reprice_worker_roundtrip_B",
        limit_price=80500.0,
        note="Worker roundtrip untouched action",
    )

    print(f"Enqueued action_id_a = {action_id_a}")
    print(f"Enqueued action_id_b = {action_id_b}")

    # 2) Submit и на двата
    submit_a = process_one_action(action_id=action_id_a, verbose=True)
    submit_b = process_one_action(action_id=action_id_b, verbose=True)

    print("\n=== submit_a ===")
    pprint(submit_a)

    print("\n=== submit_b ===")
    pprint(submit_b)

    row_a_before = fetch_action(action_id_a)
    row_b_before = fetch_action(action_id_b)

    print("\n=== row_a_before ===")
    pprint(row_a_before)

    print("\n=== row_b_before ===")
    pprint(row_b_before)

    if not row_a_before or row_a_before.get("status") != "DONE":
        raise RuntimeError(f"action_id_a not DONE: {row_a_before}")

    if not row_b_before or row_b_before.get("status") != "DONE":
        raise RuntimeError(f"action_id_b not DONE: {row_b_before}")

    old_order_id_a = str(row_a_before["api_order_id"])
    old_order_id_b = str(row_b_before["api_order_id"])

    print(f"\nold_order_id_a = {old_order_id_a}")
    print(f"old_order_id_b = {old_order_id_b}")
    print(f"old_order_a_open = {is_order_open(symbol, old_order_id_a)}")
    print(f"old_order_b_open = {is_order_open(symbol, old_order_id_b)}")

    # 3) Worker config
    service_cfg = RepriceServiceConfig(
        threshold_bps=2.0,
        favorable_threshold_bps=1.0,
        adverse_threshold_bps=10.0,
        max_order_age_sec=None,
        require_order_open=True,
        update_action_queue=True,
    )

    worker_cfg = RepriceWorkerConfig(
        service=service_cfg,
        statuses=("DONE",),
        order_kinds=("LIMIT", "POST_ONLY"),
        max_items=50,
        poll_interval_sec=1.0,
    )

    # 4) Custom resolver:
    # - само за action_id_a връща нова target цена
    # - за всички други връща None
    target_price_for_a = 80490.0

    def custom_target_price_resolver(action_row: dict):
        action_id = int(action_row["id"])
        if action_id == action_id_a:
            return target_price_for_a
        return None

    # 5) Пускаме 1 pass с apply_changes=True
    print("\n=== worker pass ===")
    worker_result = run_reprice_pass(
        target_price_resolver=custom_target_price_resolver,
        config=worker_cfg,
        apply_changes=True,
        verbose=True,
    )
    pprint(worker_result)

    # 6) Проверяваме БД след pass-а
    row_a_after = fetch_action(action_id_a)
    row_b_after = fetch_action(action_id_b)

    print("\n=== row_a_after ===")
    pprint(row_a_after)

    print("\n=== row_b_after ===")
    pprint(row_b_after)

    new_order_id_a = str(row_a_after["api_order_id"])
    current_order_id_b = str(row_b_after["api_order_id"])

    print(f"\nnew_order_id_a = {new_order_id_a}")
    print(f"current_order_id_b = {current_order_id_b}")

    print("\n=== open state checks ===")
    print(f"old_order_a_open_after = {is_order_open(symbol, old_order_id_a)}")
    print(f"new_order_a_open_after = {is_order_open(symbol, new_order_id_a)}")
    print(f"old_order_b_open_after = {is_order_open(symbol, old_order_id_b)}")
    print(f"same_order_id_for_b = {current_order_id_b == old_order_id_b}")

    print("\n=== price checks ===")
    print(f"row_a_after.limit_price = {row_a_after.get('limit_price')}")
    print(f"row_b_after.limit_price = {row_b_after.get('limit_price')}")

    print("\n=== open order item A ===")
    pprint(find_open_order(symbol, new_order_id_a))

    print("\n=== open order item B ===")
    pprint(find_open_order(symbol, current_order_id_b))
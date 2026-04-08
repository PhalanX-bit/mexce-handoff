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
from core.mexc_direct import cancel_order_raw
from core.order_manager import replace_limit_order_from_action
from core.reconcile_service import (
    get_order_state,
    get_order_state_by_api_order_id,
    reconcile_action,
)


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

    # 1) OPEN action
    action_open = enqueue_test_action(
        created_by="test_reconcile_service_open",
        limit_price=80500.0,
        note="Reconcile OPEN action",
    )
    print(f"action_open = {action_open}")
    submit_open = process_one_action(action_id=action_open, verbose=True)
    print("\n=== submit_open ===")
    pprint(submit_open)

    row_open = fetch_action(action_open)
    if not row_open or row_open.get("status") != "DONE":
        raise RuntimeError(f"OPEN action failed submit: {row_open}")

    open_order_id = str(row_open["api_order_id"])
    print(f"\nopen_order_id = {open_order_id}")

    print("\n=== get_order_state(action_open) ===")
    state_open = get_order_state(action_open)
    pprint(state_open)

    print("\n=== reconcile_action(action_open, update_action_queue=False) ===")
    rec_open = reconcile_action(action_open, update_action_queue=False)
    pprint(rec_open)

    # 2) CANCELLED action
    action_cancel = enqueue_test_action(
        created_by="test_reconcile_service_cancel",
        limit_price=80500.0,
        note="Reconcile CANCELED action",
    )
    print(f"\naction_cancel = {action_cancel}")
    submit_cancel = process_one_action(action_id=action_cancel, verbose=True)
    print("\n=== submit_cancel ===")
    pprint(submit_cancel)

    row_cancel = fetch_action(action_cancel)
    if not row_cancel or row_cancel.get("status") != "DONE":
        raise RuntimeError(f"CANCEL action failed submit: {row_cancel}")

    cancel_order_id = str(row_cancel["api_order_id"])
    print(f"\ncancel_order_id = {cancel_order_id}")

    print("\n=== cancel response ===")
    cancel_resp = cancel_order_raw(cancel_order_id)
    pprint(cancel_resp)

    print("\n=== get_order_state_by_api_order_id(cancelled) ===")
    state_cancel = get_order_state_by_api_order_id("BTC/USDT:USDT", cancel_order_id)
    pprint(state_cancel)

    print("\n=== reconcile_action(action_cancel, update_action_queue=False) ===")
    rec_cancel = reconcile_action(action_cancel, update_action_queue=False)
    pprint(rec_cancel)

    # 3) REPLACED action -> inspect old and new order ids
    action_replace = enqueue_test_action(
        created_by="test_reconcile_service_replace",
        limit_price=80500.0,
        note="Reconcile REPLACED action",
    )
    print(f"\naction_replace = {action_replace}")
    submit_replace = process_one_action(action_id=action_replace, verbose=True)
    print("\n=== submit_replace ===")
    pprint(submit_replace)

    row_replace_before = fetch_action(action_replace)
    if not row_replace_before or row_replace_before.get("status") != "DONE":
        raise RuntimeError(f"REPLACE action failed submit: {row_replace_before}")

    old_replace_order_id = str(row_replace_before["api_order_id"])
    print(f"\nold_replace_order_id = {old_replace_order_id}")

    print("\n=== replace_limit_order_from_action(...) ===")
    replace_result = replace_limit_order_from_action(
        action_id=action_replace,
        new_price=80450.0,
        old_order_id=old_replace_order_id,
        require_old_open=True,
        cancel_first=True,
        update_action_queue=True,
    )
    pprint(replace_result)

    if not replace_result.get("ok"):
        raise RuntimeError(f"Replace failed: {replace_result}")

    row_replace_after = fetch_action(action_replace)
    new_replace_order_id = str(row_replace_after["api_order_id"])
    print(f"\nnew_replace_order_id = {new_replace_order_id}")

    print("\n=== get_order_state_by_api_order_id(old replaced order) ===")
    state_old_replaced = get_order_state_by_api_order_id("BTC/USDT:USDT", old_replace_order_id)
    pprint(state_old_replaced)

    print("\n=== get_order_state(action_replace) -> should point to new order ===")
    state_new_replaced = get_order_state(action_replace)
    pprint(state_new_replaced)

    print("\n=== reconcile_action(action_replace, update_action_queue=False) ===")
    rec_replace = reconcile_action(action_replace, update_action_queue=False)
    pprint(rec_replace)

    print("\n==================== SUMMARY ====================")
    print(f"OPEN lifecycle_state      = {state_open.get('lifecycle_state')}")
    print(f"CANCELED lifecycle_state  = {state_cancel.get('lifecycle_state')}")
    print(f"OLD REPLACED lifecycle    = {state_old_replaced.get('lifecycle_state')}")
    print(f"NEW REPLACED lifecycle    = {state_new_replaced.get('lifecycle_state')}")
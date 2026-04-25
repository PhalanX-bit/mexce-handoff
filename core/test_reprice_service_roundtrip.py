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
from core.reprice_service import (
    RepriceServiceConfig,
    run_reprice_cycle_for_action,
    run_reprice_loop_for_action,
)


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
            "test_reprice_service_roundtrip",
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
            "API-only reprice service roundtrip test",
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

    # 1) evaluate-only cycle -> no DB mutation
    eval_target_price = 80510.0

    # 2) apply cycle -> replace expected for OPEN SHORT with favorable_down
    apply_target_price = 80490.0

    # 3) loop mode -> usually no-op after already repriced to 80490
    loop_target_price = 80490.0

    cfg = RepriceServiceConfig(
        threshold_bps=2.0,
        favorable_threshold_bps=1.0,
        adverse_threshold_bps=10.0,
        max_order_age_sec=None,
        require_order_open=True,
        update_action_queue=True,
    )

    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    action_id = enqueue_test_action(limit_price=initial_price)
    print(f"Enqueued action id = {action_id}")

    submit_result = process_one_action(action_id=action_id, verbose=True)

    print("\n=== process_one_action returned ===")
    pprint(submit_result)

    row_after_submit = fetch_action(action_id)
    print("\n=== row after initial submit ===")
    pprint(row_after_submit)

    if not row_after_submit:
        raise RuntimeError("No DB row found after initial submit.")

    if row_after_submit.get("status") != "DONE":
        raise RuntimeError(f"Initial submit did not finish as DONE. status={row_after_submit.get('status')}")

    initial_order_id = row_after_submit.get("api_order_id")
    if initial_order_id is None:
        raise RuntimeError("api_order_id is missing after initial submit.")

    initial_order_id = str(initial_order_id)
    print(f"\ninitial_order_id = {initial_order_id}")
    print(f"initial_order_open = {is_order_open(symbol, initial_order_id)}")

    print("\n====================================")
    print("=== STEP 1: evaluate-only cycle ===")
    print("====================================")
    eval_cycle = run_reprice_cycle_for_action(
        action_id=action_id,
        target_price=eval_target_price,
        config=cfg,
        apply_changes=False,
    )
    pprint(eval_cycle)

    row_after_eval_cycle = fetch_action(action_id)
    print("\n=== row after evaluate-only cycle ===")
    pprint(row_after_eval_cycle)

    eval_cycle_order_id = str(row_after_eval_cycle["api_order_id"])
    print(f"\neval_cycle_order_id = {eval_cycle_order_id}")
    print(f"same_order_after_eval_cycle = {eval_cycle_order_id == initial_order_id}")
    print(f"order_open_after_eval_cycle = {is_order_open(symbol, eval_cycle_order_id)}")

    print("\n===============================")
    print("=== STEP 2: apply one cycle ===")
    print("===============================")
    apply_cycle = run_reprice_cycle_for_action(
        action_id=action_id,
        target_price=apply_target_price,
        config=cfg,
        apply_changes=True,
    )
    pprint(apply_cycle)

    row_after_apply_cycle = fetch_action(action_id)
    print("\n=== row after apply cycle ===")
    pprint(row_after_apply_cycle)

    apply_cycle_order_id = str(row_after_apply_cycle["api_order_id"])
    print(f"\napply_cycle_order_id = {apply_cycle_order_id}")
    print(f"initial_order_open_after_apply_cycle = {is_order_open(symbol, initial_order_id)}")
    print(f"apply_cycle_order_open = {is_order_open(symbol, apply_cycle_order_id)}")
    print(f"db_limit_price_after_apply_cycle = {row_after_apply_cycle.get('limit_price')}")

    print("\n=== current open order item after apply cycle ===")
    pprint(find_open_order(symbol, apply_cycle_order_id))

    print("\n========================================")
    print("=== STEP 3: loop mode (likely NOOP) ===")
    print("========================================")
    loop_result = run_reprice_loop_for_action(
        action_id=action_id,
        target_price=loop_target_price,
        config=cfg,
        apply_changes=True,
        interval_sec=1.0,
        max_cycles=3,
        verbose=True,
    )
    pprint(loop_result)

    row_after_loop = fetch_action(action_id)
    print("\n=== row after loop ===")
    pprint(row_after_loop)

    loop_order_id = str(row_after_loop["api_order_id"])
    print(f"\nloop_order_id = {loop_order_id}")
    print(f"same_order_after_loop = {loop_order_id == apply_cycle_order_id}")
    print(f"loop_order_open = {is_order_open(symbol, loop_order_id)}")
    print(f"db_limit_price_after_loop = {row_after_loop.get('limit_price')}")

    print("\n=== final open order item ===")
    pprint(find_open_order(symbol, loop_order_id))
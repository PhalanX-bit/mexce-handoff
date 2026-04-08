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
from core.stale_policy import apply_reprice_decision, evaluate_reprice_decision


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
            "test_overshoot_policy_roundtrip",
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
            "API-only overshoot policy roundtrip test",
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

    # За OPEN SHORT:
    # - target надолу => favorable_down
    # - target нагоре => adverse_up
    favorable_target_price = 80490.0
    adverse_target_price = 80510.0

    # Специално различни прагове:
    # favorable = 1 bps
    # adverse   = 10 bps
    default_threshold_bps = 2.0
    favorable_threshold_bps = 1.0
    adverse_threshold_bps = 10.0

    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {Path(DB_PATH).resolve()}")

    action_id = enqueue_test_action(limit_price=initial_price)
    print(f"Enqueued action id = {action_id}")

    result = process_one_action(action_id=action_id, verbose=True)

    print("\n=== process_one_action returned ===")
    pprint(result)

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

    print("\n======================================================")
    print("=== SCENARIO 1: EVALUATE favorable_down for OPEN SHORT ===")
    print("======================================================")
    eval_favorable = evaluate_reprice_decision(
        action_id=action_id,
        target_price=favorable_target_price,
        threshold_bps=default_threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_order_age_sec=None,
    )
    pprint(eval_favorable)

    row_after_eval_favorable = fetch_action(action_id)
    print("\n=== row after evaluate favorable ===")
    pprint(row_after_eval_favorable)

    print(f"\norder_id_after_eval_favorable = {row_after_eval_favorable.get('api_order_id')}")
    print(f"same_order_after_eval_favorable = {str(row_after_eval_favorable.get('api_order_id')) == initial_order_id}")

    print("\n===================================================")
    print("=== SCENARIO 2: EVALUATE adverse_up for OPEN SHORT ===")
    print("===================================================")
    eval_adverse = evaluate_reprice_decision(
        action_id=action_id,
        target_price=adverse_target_price,
        threshold_bps=default_threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_order_age_sec=None,
    )
    pprint(eval_adverse)

    row_after_eval_adverse = fetch_action(action_id)
    print("\n=== row after evaluate adverse ===")
    pprint(row_after_eval_adverse)

    print(f"\norder_id_after_eval_adverse = {row_after_eval_adverse.get('api_order_id')}")
    print(f"same_order_after_eval_adverse = {str(row_after_eval_adverse.get('api_order_id')) == initial_order_id}")

    print("\n===========================================================")
    print("=== SCENARIO 3: APPLY favorable_down should REPLACE early ===")
    print("===========================================================")
    apply_favorable = apply_reprice_decision(
        action_id=action_id,
        target_price=favorable_target_price,
        threshold_bps=default_threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_order_age_sec=None,
        require_order_open=True,
        update_action_queue=True,
    )
    pprint(apply_favorable)

    row_after_apply_favorable = fetch_action(action_id)
    print("\n=== row after apply favorable ===")
    pprint(row_after_apply_favorable)

    favorable_new_order_id = str(row_after_apply_favorable["api_order_id"])
    print(f"\nfavorable_new_order_id = {favorable_new_order_id}")
    print(f"initial_order_open_after_favorable_apply = {is_order_open(symbol, initial_order_id)}")
    print(f"favorable_new_order_open = {is_order_open(symbol, favorable_new_order_id)}")
    print(f"db_limit_price_after_favorable_apply = {row_after_apply_favorable.get('limit_price')}")

    print("\n=== initial open order item after favorable apply ===")
    pprint(find_open_order(symbol, initial_order_id))

    print("\n=== favorable new open order item ===")
    pprint(find_open_order(symbol, favorable_new_order_id))

    print("\n====================================================================")
    print("=== SCENARIO 4: APPLY adverse_up should likely NOOP with high threshold ===")
    print("====================================================================")
    apply_adverse = apply_reprice_decision(
        action_id=action_id,
        target_price=adverse_target_price,
        threshold_bps=default_threshold_bps,
        favorable_threshold_bps=favorable_threshold_bps,
        adverse_threshold_bps=adverse_threshold_bps,
        max_order_age_sec=None,
        require_order_open=True,
        update_action_queue=True,
    )
    pprint(apply_adverse)

    row_after_apply_adverse = fetch_action(action_id)
    print("\n=== row after apply adverse ===")
    pprint(row_after_apply_adverse)

    adverse_current_order_id = str(row_after_apply_adverse["api_order_id"])
    print(f"\nadverse_current_order_id = {adverse_current_order_id}")
    print(f"same_order_after_adverse_apply = {adverse_current_order_id == favorable_new_order_id}")
    print(f"adverse_current_order_open = {is_order_open(symbol, adverse_current_order_id)}")
    print(f"db_limit_price_after_adverse_apply = {row_after_apply_adverse.get('limit_price')}")

    print("\n=== current open order item after adverse apply ===")
    pprint(find_open_order(symbol, adverse_current_order_id))
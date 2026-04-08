from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from core.db import DB_PATH
from core.reprice_service import RepriceServiceConfig
from core.reprice_worker import RepriceWorkerConfig, run_reprice_pass


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def ensure_columns_exist() -> Dict[str, bool]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
        cols = {row["name"] for row in rows}
        return {
            "ops_lock_token": "ops_lock_token" in cols,
            "ops_locked_at": "ops_locked_at" in cols,
            "last_reprice_payload": "last_reprice_payload" in cols,
        }
    finally:
        conn.close()


def fetch_candidate_row(
    *,
    symbol: Optional[str] = None,
    created_by_prefix: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql = """
        SELECT *
        FROM action_queue
        WHERE status = 'DONE'
          AND order_kind IN ('LIMIT', 'POST_ONLY')
          AND api_order_id IS NOT NULL
        """
        params = []

        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)

        if created_by_prefix:
            sql += " AND created_by LIKE ?"
            params.append(f"{created_by_prefix}%")

        sql += " ORDER BY priority DESC, id ASC LIMIT 1"

        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_action_debug(action_id: int) -> Dict[str, Any]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT
                id,
                status,
                symbol,
                order_kind,
                panel_mode,
                side,
                limit_price,
                api_order_id,
                api_client_oid,
                api_mode,
                last_error,
                ops_lock_token,
                ops_locked_at,
                last_reprice_payload
            FROM action_queue
            WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def fixed_target_price_resolver(action_row: Dict[str, Any]) -> Optional[float]:
    """
    Safe smoke-test resolver:
    връща текущата limit_price, за да не насилваме drift.
    Това пак трябва да мине през evaluate path и да запише payload.
    """
    value = action_row.get("limit_price")
    if value in (None, ""):
        return None
    return float(value)


def main() -> None:
    print("DB_PATH =", Path(DB_PATH).resolve())

    cols = ensure_columns_exist()
    print("COLUMN CHECK:")
    print(safe_json(cols))

    missing = [k for k, v in cols.items() if not v]
    if missing:
        print("ERROR: missing columns:", missing)
        print("Първо пусни lean_hardening_schema.")
        return

    candidate = fetch_candidate_row()
    if not candidate:
        print("ERROR: няма подходящ кандидат в action_queue")
        print("Трябва да има поне 1 ред със:")
        print("- status='DONE'")
        print("- order_kind IN ('LIMIT','POST_ONLY')")
        print("- api_order_id IS NOT NULL")
        return

    action_id = int(candidate["id"])
    print("SELECTED ACTION:")
    print(
        safe_json(
            {
                "id": candidate.get("id"),
                "symbol": candidate.get("symbol"),
                "status": candidate.get("status"),
                "order_kind": candidate.get("order_kind"),
                "panel_mode": candidate.get("panel_mode"),
                "side": candidate.get("side"),
                "limit_price": candidate.get("limit_price"),
                "api_order_id": candidate.get("api_order_id"),
                "created_by": candidate.get("created_by"),
            }
        )
    )

    cfg = RepriceWorkerConfig(
        service=RepriceServiceConfig(
            threshold_bps=2.0,
            favorable_threshold_bps=1.0,
            adverse_threshold_bps=10.0,
            max_order_age_sec=None,
            require_order_open=True,
            update_action_queue=True,
        ),
        statuses=("DONE",),
        order_kinds=("LIMIT", "POST_ONLY"),
        max_items=20,
        poll_interval_sec=2.0,
        symbols=(str(candidate["symbol"]),),
    )

    print("\nRUNNING EVALUATE-ONLY PASS ...")
    result = run_reprice_pass(
        target_price_resolver=fixed_target_price_resolver,
        config=cfg,
        apply_changes=False,
        verbose=True,
    )

    print("\nPASS RESULT:")
    print(safe_json(result))

    debug_row = fetch_action_debug(action_id)

    print("\nPOST-RUN ACTION DEBUG:")
    print(
        safe_json(
            {
                "id": debug_row.get("id"),
                "status": debug_row.get("status"),
                "symbol": debug_row.get("symbol"),
                "limit_price": debug_row.get("limit_price"),
                "api_order_id": debug_row.get("api_order_id"),
                "api_client_oid": debug_row.get("api_client_oid"),
                "api_mode": debug_row.get("api_mode"),
                "last_error": debug_row.get("last_error"),
                "ops_lock_token": debug_row.get("ops_lock_token"),
                "ops_locked_at": debug_row.get("ops_locked_at"),
            }
        )
    )

    print("\nRAW last_reprice_payload:")
    print(debug_row.get("last_reprice_payload"))


if __name__ == "__main__":
    main()
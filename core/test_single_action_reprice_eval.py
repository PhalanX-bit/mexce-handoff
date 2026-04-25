from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from core.db import DB_PATH
from core.reprice_service import RepriceServiceConfig
from core.reprice_action_once_hardened import reprice_action_once_hardened


ACTION_ID = 243
TARGET_PRICE = 80959.5
APPLY_CHANGES = True
AUTO_CREATED_BY = "seed_live_open_order_action"


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def resolve_action_id(explicit_action_id: Optional[int]) -> Optional[int]:
    if explicit_action_id is not None:
        return int(explicit_action_id)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id
            FROM action_queue
            WHERE created_by = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (AUTO_CREATED_BY,),
        ).fetchone()
        return int(row["id"]) if row else None
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
                created_by,
                status,
                symbol,
                order_kind,
                panel_mode,
                side,
                qty,
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


def main() -> None:
    actual_action_id = resolve_action_id(ACTION_ID)

    print("DB_PATH =", Path(DB_PATH).resolve())
    print("ACTION_ID =", actual_action_id)
    print("TARGET_PRICE =", TARGET_PRICE)
    print("APPLY_CHANGES =", APPLY_CHANGES)
    print("AUTO_CREATED_BY =", AUTO_CREATED_BY)

    if actual_action_id is None:
        print("ERROR: no action found")
        print("Първо пусни: python -m core.seed_live_open_order_action")
        return

    before_row = fetch_action_debug(actual_action_id)
    if not before_row:
        print("ERROR: action not found")
        return

    print("\nBEFORE:")
    print(
        safe_json(
            {
                "id": before_row.get("id"),
                "created_by": before_row.get("created_by"),
                "status": before_row.get("status"),
                "symbol": before_row.get("symbol"),
                "order_kind": before_row.get("order_kind"),
                "panel_mode": before_row.get("panel_mode"),
                "side": before_row.get("side"),
                "qty": before_row.get("qty"),
                "limit_price": before_row.get("limit_price"),
                "api_order_id": before_row.get("api_order_id"),
                "api_client_oid": before_row.get("api_client_oid"),
                "api_mode": before_row.get("api_mode"),
                "last_error": before_row.get("last_error"),
                "ops_lock_token": before_row.get("ops_lock_token"),
                "ops_locked_at": before_row.get("ops_locked_at"),
            }
        )
    )

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        result = reprice_action_once_hardened(
            conn=conn,
            action_id=actual_action_id,
            target_price=float(TARGET_PRICE),
            config=RepriceServiceConfig(
                threshold_bps=2.0,
                favorable_threshold_bps=1.0,
                adverse_threshold_bps=10.0,
                max_order_age_sec=None,
                require_order_open=True,
                update_action_queue=True,
            ),
            apply_changes=APPLY_CHANGES,
            require_order_open=True,
            lock_timeout_sec=90,
            table_name="action_queue",
        )
    finally:
        conn.close()

    print("\nRESULT:")
    print(safe_json(result))

    after_row = fetch_action_debug(actual_action_id)

    print("\nAFTER:")
    print(
        safe_json(
            {
                "id": after_row.get("id"),
                "created_by": after_row.get("created_by"),
                "status": after_row.get("status"),
                "symbol": after_row.get("symbol"),
                "order_kind": after_row.get("order_kind"),
                "panel_mode": after_row.get("panel_mode"),
                "side": after_row.get("side"),
                "qty": after_row.get("qty"),
                "limit_price": after_row.get("limit_price"),
                "api_order_id": after_row.get("api_order_id"),
                "api_client_oid": after_row.get("api_client_oid"),
                "api_mode": after_row.get("api_mode"),
                "last_error": after_row.get("last_error"),
                "ops_lock_token": after_row.get("ops_lock_token"),
                "ops_locked_at": after_row.get("ops_locked_at"),
            }
        )
    )

    print("\nRAW last_reprice_payload:")
    print(after_row.get("last_reprice_payload"))


if __name__ == "__main__":
    main()
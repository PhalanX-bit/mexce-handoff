from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from core.db import DB_PATH


SOURCE_ACTION_ID = 198

LIVE_ORDER = {
    "orderId": "795491426745013760",
    "symbol": "BTC_USDT",
    "price": 81000,
    "priceStr": "81000",
    "vol": 1,
    "leverage": 500,
    "side": 3,
    "category": 1,
    "orderType": 1,
    "openType": 2,
    "state": 2,
    "externalOid": "_m_f11a2453fb0749bfa91a9ca3c1dc6a63",
    "createTime": 1775330335534,
    "updateTime": 1775330335657,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def get_columns(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
    cols = []
    for row in rows:
        cols.append(row[1])
    return cols


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        src = conn.execute(
            """
            SELECT *
            FROM action_queue
            WHERE id = ?
            """,
            (SOURCE_ACTION_ID,),
        ).fetchone()

        if src is None:
            print({"ok": False, "reason": "source_action_not_found", "source_action_id": SOURCE_ACTION_ID})
            return

        src_dict: Dict[str, Any] = dict(src)
        cols = get_columns(conn)

        new_row = dict(src_dict)
        new_row.pop("id", None)

        # core identity
        if "created_at" in cols:
            new_row["created_at"] = utc_now_iso()
        if "created_by" in cols:
            new_row["created_by"] = "seed_live_open_order_action"
        if "status" in cols:
            new_row["status"] = "DONE"

        # live order linkage
        if "symbol" in cols:
            new_row["symbol"] = "BTC/USDT:USDT"
        if "panel_mode" in cols:
            new_row["panel_mode"] = "OPEN"
        if "side" in cols:
            new_row["side"] = "SHORT"
        if "order_kind" in cols:
            new_row["order_kind"] = "LIMIT"
        if "qty" in cols:
            new_row["qty"] = float(LIVE_ORDER["vol"])
        if "limit_price" in cols:
            new_row["limit_price"] = float(LIVE_ORDER["price"])
        if "leverage" in cols:
            new_row["leverage"] = int(LIVE_ORDER["leverage"])

        if "api_order_id" in cols:
            new_row["api_order_id"] = str(LIVE_ORDER["orderId"])
        if "api_client_oid" in cols:
            new_row["api_client_oid"] = str(LIVE_ORDER["externalOid"])
        if "api_mode" in cols:
            new_row["api_mode"] = "seed_live_open_order"
        if "api_submit_path" in cols:
            new_row["api_submit_path"] = "/seed/live_open_order"
        if "api_response" in cols:
            new_row["api_response"] = safe_json(LIVE_ORDER)

        # cleanup operational state
        if "last_error" in cols:
            new_row["last_error"] = None
        if "reconcile_state" in cols:
            new_row["reconcile_state"] = None
        if "reconcile_reason" in cols:
            new_row["reconcile_reason"] = None
        if "reconcile_payload" in cols:
            new_row["reconcile_payload"] = None
        if "reconcile_checked_at" in cols:
            new_row["reconcile_checked_at"] = None
        if "ops_lock_token" in cols:
            new_row["ops_lock_token"] = None
        if "ops_locked_at" in cols:
            new_row["ops_locked_at"] = None
        if "last_reprice_payload" in cols:
            new_row["last_reprice_payload"] = None
        if "last_update_at" in cols:
            new_row["last_update_at"] = utc_now_iso()

        insert_cols = [c for c in cols if c != "id" and c in new_row]
        placeholders = ",".join("?" for _ in insert_cols)
        sql = f"""
        INSERT INTO action_queue ({",".join(insert_cols)})
        VALUES ({placeholders})
        """
        values = [new_row[c] for c in insert_cols]
        cur = conn.execute(sql, values)
        conn.commit()

        new_id = cur.lastrowid

        print(
            {
                "ok": True,
                "db_path": str(Path(DB_PATH).resolve()),
                "source_action_id": SOURCE_ACTION_ID,
                "new_action_id": new_id,
                "api_order_id": new_row.get("api_order_id"),
                "symbol": new_row.get("symbol"),
                "panel_mode": new_row.get("panel_mode"),
                "side": new_row.get("side"),
                "limit_price": new_row.get("limit_price"),
                "qty": new_row.get("qty"),
            }
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
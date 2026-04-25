from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import connect


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def table_info_map(con, table_name: str) -> Dict[str, Dict[str, Any]]:
    rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        out[str(r["name"])] = dict(r)
    return out


def build_action_row(schema: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ts = now_utc_iso()

    # Базов безопасен тестов ордер
    base: Dict[str, Any] = {
        "created_at": ts,
        "last_update_at": ts,
        "created_by": "test_enqueue_api_order",
        "symbol": "BTC/USDT:USDT",
        "exchange": "MEXC",
        "market_type": "swap",
        "panel_mode": "OPEN",
        "side": "SHORT",
        "qty": 1.0,
        "limit_price": 80500.0,
        "order_kind": "LIMIT",
        "order_type": "limit",
        "status": "ARMED",
        "priority": 10,
        "attempts": 0,
        "last_error": None,
        "note": "test enqueue for api_executor",
        "leverage": 500,
        "reduce_only": 0,
    }

    # Ако в схемата има други NOT NULL полета без default, опитваме разумни стойности
    for col, meta in schema.items():
        notnull = int(meta.get("notnull") or 0) == 1
        default_value = meta.get("dflt_value")
        col_type = str(meta.get("type") or "").upper()

        if col in base:
            continue
        if not notnull:
            continue
        if default_value is not None:
            continue
        if col == "id":
            continue

        # Разумни fallback-и
        if col_type.startswith("INT"):
            base[col] = 0
        elif col_type.startswith("REAL") or col_type.startswith("FLOAT") or col_type.startswith("NUM"):
            base[col] = 0.0
        else:
            base[col] = ""

    return base


def main() -> None:
    con = connect()
    try:
        schema = table_info_map(con, "action_queue")

        print("=== ACTION_QUEUE SCHEMA (required fields) ===")
        for name, meta in schema.items():
            if int(meta.get("notnull") or 0) == 1 and meta.get("dflt_value") is None and name != "id":
                print(
                    {
                        "name": name,
                        "type": meta.get("type"),
                        "notnull": meta.get("notnull"),
                        "default": meta.get("dflt_value"),
                    }
                )

        row_data = build_action_row(schema)

        insert_cols = [c for c in row_data.keys() if c in schema]
        insert_vals = [row_data[c] for c in insert_cols]
        placeholders = ",".join("?" for _ in insert_cols)
        col_sql = ", ".join(insert_cols)

        sql = f"""
            INSERT INTO action_queue ({col_sql})
            VALUES ({placeholders})
        """

        cur = con.execute(sql, insert_vals)
        action_id = safe_int(cur.lastrowid)
        con.commit()

        row = con.execute(
            """
            SELECT *
            FROM action_queue
            WHERE id=?
            """,
            (action_id,),
        ).fetchone()

        print()
        print("=== ENQUEUED ACTION ===")
        print(dict(row) if row else {"id": action_id, "status": "missing_after_insert"})
        print()
        print("Now run:")
        print("python -m core.api_executor")

    finally:
        con.close()


if __name__ == "__main__":
    main()
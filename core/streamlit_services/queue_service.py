from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from core.action_queue_order import ACTION_QUEUE_EXECUTOR_ORDER_BY
from core.symbol_utils import canonical_futures_symbol
from core.streamlit_services.action_ledger_service import (
    build_action_queue_ledger_note,
    log_action_queue_event,
)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_insert_v2(con, payload: dict) -> int:
    payload = dict(payload)
    payload["idempotency_key"] = uuid.uuid4().hex

    raw_symbol = payload.get("symbol")
    canonical_symbol = canonical_futures_symbol(raw_symbol)
    if canonical_symbol:
        payload["symbol"] = canonical_symbol

    cur = con.execute(
        """
        INSERT INTO action_queue (
          created_at, created_by, exchange, market_type, symbol, intent, side,
          reduce_only, qty, qty_unit, order_type, limit_price, slippage_bps,
          trigger_type, trigger_op, trigger_price, trigger_timeout_sec,
          min_free_margin, max_margin_ratio, max_spread_bps,
          status, priority, idempotency_key, attempts, last_error, last_update_at,
          ui_hint, note, panel_mode, order_kind, leverage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["created_at"],
            payload.get("created_by", "streamlit"),
            payload.get("exchange", "mexc"),
            payload.get("market_type", "swap"),
            payload["symbol"],
            payload.get("intent", "close"),
            payload.get("side"),
            int(payload.get("reduce_only", 1)),
            float(payload["qty"]),
            payload.get("qty_unit", "contracts"),
            payload.get("order_type", "market"),
            payload.get("limit_price"),
            payload.get("slippage_bps"),
            payload.get("trigger_type", "manual"),
            payload.get("trigger_op"),
            payload.get("trigger_price"),
            payload.get("trigger_timeout_sec"),
            payload.get("min_free_margin"),
            payload.get("max_margin_ratio"),
            payload.get("max_spread_bps"),
            payload.get("status", "PENDING"),
            int(payload.get("priority", 100)),
            payload["idempotency_key"],
            int(payload.get("attempts", 0)),
            payload.get("last_error"),
            payload.get("last_update_at"),
            payload.get("ui_hint"),
            payload.get("note"),
            payload.get("panel_mode", "CLOSE"),
            payload.get("order_kind", "LIMIT"),
            payload.get("leverage"),
        ),
    )
    action_id = int(cur.lastrowid)

    log_action_queue_event(
        con,
        created_at=payload["created_at"],
        action_id=action_id,
        symbol=payload["symbol"],
        event_type="QUEUE_CREATED",
        side=payload.get("side"),
        qty=float(payload["qty"]),
        price=payload.get("limit_price"),
        note=build_action_queue_ledger_note(
            action_id,
            payload.get("status", "PENDING"),
            f"panel_mode={payload.get('panel_mode', 'CLOSE')} | order_kind={payload.get('order_kind', 'LIMIT')}",
        ),
    )
    return int(cur.rowcount)


def queue_list_v2(con, status=None, limit: int = 200):
    if status and status != "ALL":
        rows = con.execute(
            f"""
            SELECT *
            FROM action_queue
            WHERE status = ?
            ORDER BY {ACTION_QUEUE_EXECUTOR_ORDER_BY}
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT *
            FROM action_queue
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_action_by_id(con, action_id: int):
    row = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE id = ?
        LIMIT 1
        """,
        (action_id,),
    ).fetchone()
    return dict(row) if row else None


def get_action_queue_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM action_queue
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "ARMED": 0,
        "RUNNING": 0,
        "DONE": 0,
        "FAILED": 0,
        "CANCELED": 0,
    }

    for row in rows:
        out[str(row["status"]).upper()] = int(row["cnt"])

    return out


def queue_arm(con, action_id: int):
    con.execute(
        """
        UPDATE action_queue
        SET status = 'ARMED',
            last_update_at = ?
        WHERE id = ?
          AND status = 'PENDING'
        """,
        (now_utc_iso(), action_id),
    )
    row = get_action_by_id(con, action_id)
    if row and str(row.get("status") or "").upper() == "ARMED":
        log_action_queue_event(
            con,
            created_at=row.get("last_update_at") or now_utc_iso(),
            action_id=action_id,
            symbol=row.get("symbol"),
            event_type="QUEUE_ARMED",
            side=row.get("side"),
            qty=row.get("qty"),
            price=row.get("limit_price"),
            note=build_action_queue_ledger_note(action_id, "ARMED"),
        )


def queue_cancel(con, action_id: int):
    con.execute(
        """
        UPDATE action_queue
        SET status = 'CANCELED',
            last_update_at = ?
        WHERE id = ?
          AND status IN ('PENDING', 'ARMED')
        """,
        (now_utc_iso(), action_id),
    )
    row = get_action_by_id(con, action_id)
    if row and str(row.get("status") or "").upper() == "CANCELED":
        log_action_queue_event(
            con,
            created_at=row.get("last_update_at") or now_utc_iso(),
            action_id=action_id,
            symbol=row.get("symbol"),
            event_type="QUEUE_CANCELED",
            side=row.get("side"),
            qty=row.get("qty"),
            price=row.get("limit_price"),
            note=build_action_queue_ledger_note(action_id, "CANCELED"),
        )


def delete_action_queue_by_statuses(con, statuses: list[str]) -> int:
    if not statuses:
        return 0

    placeholders = ",".join("?" for _ in statuses)
    cur = con.execute(
        f"""
        DELETE FROM action_queue
        WHERE status IN ({placeholders})
        """,
        tuple(statuses),
    )
    con.commit()
    return int(cur.rowcount)


def delete_action_queue_row(con, action_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM action_queue
        WHERE id = ?
        """,
        (action_id,),
    )
    con.commit()
    return int(cur.rowcount)


def get_db_files(con):
    rows = con.execute("PRAGMA database_list;").fetchall()
    out = []

    for row in rows:
        try:
            out.append(
                {
                    "seq": row[0],
                    "name": row[1],
                    "file": row[2],
                }
            )
        except Exception:
            out.append({"row": str(row)})

    return out


def reset_running(con, symbol: Optional[str] = None, also_reset_armed: bool = False) -> int:
    statuses = ["RUNNING"]
    if also_reset_armed:
        statuses.append("ARMED")

    placeholders = ",".join("?" for _ in statuses)

    affected_rows = []
    if symbol:
        normalized_symbol = canonical_futures_symbol(symbol) or symbol
        affected_rows = con.execute(
            f"""
            SELECT id, symbol, side, qty, limit_price
            FROM action_queue
            WHERE symbol = ?
              AND status IN ({placeholders})
            """,
            (normalized_symbol, *statuses),
        ).fetchall()
        cur = con.execute(
            f"""
            UPDATE action_queue
            SET status = 'FAILED',
                last_error = COALESCE(last_error, 'manual reset (stuck)'),
                last_update_at = datetime('now')
            WHERE symbol = ?
              AND status IN ({placeholders})
            """,
            (normalized_symbol, *statuses),
        )
    else:
        affected_rows = con.execute(
            f"""
            SELECT id, symbol, side, qty, limit_price
            FROM action_queue
            WHERE status IN ({placeholders})
            """,
            tuple(statuses),
        ).fetchall()
        cur = con.execute(
            f"""
            UPDATE action_queue
            SET status = 'FAILED',
                last_error = COALESCE(last_error, 'manual reset (stuck)'),
                last_update_at = datetime('now')
            WHERE status IN ({placeholders})
            """,
            tuple(statuses),
        )

    ts = now_utc_iso()
    for row in affected_rows:
        log_action_queue_event(
            con,
            created_at=ts,
            action_id=int(row["id"]),
            symbol=row["symbol"],
            event_type="QUEUE_RESET_FAILED",
            side=row["side"],
            qty=row["qty"],
            price=row["limit_price"],
            note=build_action_queue_ledger_note(int(row["id"]), "FAILED", "manual reset (stuck)"),
        )

    con.commit()
    return int(cur.rowcount)

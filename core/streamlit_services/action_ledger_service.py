from __future__ import annotations

from typing import Any, Optional

from core.symbol_utils import canonical_futures_symbol


def add_action_ledger(con, created_at, symbol, action_type, side=None, qty=None, price=None, note=None):
    con.execute(
        """
        INSERT INTO actions_ledger (created_at, symbol, action_type, side, qty, price, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, symbol, action_type, side, qty, price, note),
    )


def build_action_queue_ledger_note(action_id: int, status: str, extra: Optional[str] = None) -> str:
    base = f"action_id={int(action_id)} | status={str(status).upper()}"
    if extra:
        return f"{base} | {str(extra).strip()}"
    return base


def build_reconcile_ledger_note(
    action_id: int,
    lifecycle_state: str,
    *,
    deal_qty: Optional[float] = None,
    resolved_avg_price: Optional[float] = None,
    extra: Optional[str] = None,
) -> str:
    parts = [
        f"action_id={int(action_id)}",
        f"lifecycle_state={str(lifecycle_state or '').upper()}",
    ]
    if deal_qty is not None:
        parts.append(f"deal_qty={float(deal_qty)}")
    if resolved_avg_price is not None:
        parts.append(f"resolved_avg_price={float(resolved_avg_price)}")
    if extra:
        parts.append(str(extra).strip())
    return " | ".join(parts)


def build_reprice_ledger_note(
    action_id: int,
    stage: str,
    *,
    current_order_price: Optional[float] = None,
    target_price: Optional[float] = None,
    drift_bps: Optional[float] = None,
    previous_order_id: Optional[str] = None,
    new_order_id: Optional[str] = None,
    extra: Optional[str] = None,
) -> str:
    parts = [
        f"action_id={int(action_id)}",
        f"stage={str(stage or '').upper()}",
    ]
    if current_order_price is not None:
        parts.append(f"current_order_price={float(current_order_price)}")
    if target_price is not None:
        parts.append(f"target_price={float(target_price)}")
    if drift_bps is not None:
        parts.append(f"drift_bps={float(drift_bps)}")
    if previous_order_id:
        parts.append(f"previous_order_id={str(previous_order_id)}")
    if new_order_id:
        parts.append(f"new_order_id={str(new_order_id)}")
    if extra:
        parts.append(str(extra).strip())
    return " | ".join(parts)


def log_action_queue_event(
    con,
    *,
    created_at: str,
    action_id: int,
    symbol: str,
    event_type: str,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    note: Optional[str] = None,
) -> bool:
    canonical_symbol = canonical_futures_symbol(symbol) or str(symbol or "").strip().upper()
    event_type = str(event_type or "").strip().upper()
    note_text = str(note or "").strip() or None
    event_marker = f"action_id={int(action_id)}"

    existing = con.execute(
        """
        SELECT id
        FROM actions_ledger
        WHERE action_type = ?
          AND symbol = ?
          AND note LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            event_type,
            canonical_symbol,
            f"%{event_marker}%",
        ),
    ).fetchone()
    if existing is not None:
        return False

    add_action_ledger(
        con,
        created_at=created_at,
        symbol=canonical_symbol,
        action_type=event_type,
        side=(str(side).strip().upper() if side else None),
        qty=float(qty) if qty is not None else None,
        price=float(price) if price is not None else None,
        note=note_text,
    )
    return True


def log_reconcile_event(
    con,
    *,
    created_at: str,
    action_id: int,
    symbol: str,
    lifecycle_state: str,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    note: Optional[str] = None,
) -> bool:
    event_type = f"RECONCILE_{str(lifecycle_state or 'UNKNOWN').strip().upper()}"
    return log_action_queue_event(
        con,
        created_at=created_at,
        action_id=action_id,
        symbol=symbol,
        event_type=event_type,
        side=side,
        qty=qty,
        price=price,
        note=note,
    )


def log_reprice_event(
    con,
    *,
    created_at: str,
    action_id: int,
    symbol: str,
    stage: str,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    note: Optional[str] = None,
) -> bool:
    canonical_symbol = canonical_futures_symbol(symbol) or str(symbol or "").strip().upper()
    event_type = f"REPRICE_{str(stage or 'UNKNOWN').strip().upper()}"
    note_text = str(note or "").strip() or None

    existing = con.execute(
        """
        SELECT id
        FROM actions_ledger
        WHERE action_type = ?
          AND symbol = ?
          AND COALESCE(note, '') = COALESCE(?, '')
        ORDER BY id DESC
        LIMIT 1
        """,
        (event_type, canonical_symbol, note_text),
    ).fetchone()
    if existing is not None:
        return False

    add_action_ledger(
        con,
        created_at=created_at,
        symbol=canonical_symbol,
        action_type=event_type,
        side=(str(side).strip().upper() if side else None),
        qty=float(qty) if qty is not None else None,
        price=float(price) if price is not None else None,
        note=note_text,
    )
    return True


def get_recent_actions(con, limit=50):
    rows = con.execute(
        """
        SELECT id, created_at, symbol, action_type, side, qty, price, note
        FROM actions_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_action_ledger_types(con) -> list[str]:
    rows = con.execute(
        """
        SELECT DISTINCT action_type
        FROM actions_ledger
        WHERE action_type IS NOT NULL
          AND action_type <> ''
        ORDER BY action_type ASC
        """
    ).fetchall()
    return [str(r["action_type"]) for r in rows]


def get_action_ledger_summary(con) -> dict[str, int]:
    row = con.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            SUM(CASE WHEN action_type LIKE 'QUEUE_%' OR action_type LIKE 'EXECUTOR_%' OR action_type LIKE 'RECONCILE_%' OR action_type LIKE 'REPRICE_%' THEN 1 ELSE 0 END) AS system_rows,
            SUM(CASE WHEN action_type NOT LIKE 'QUEUE_%' AND action_type NOT LIKE 'EXECUTOR_%' AND action_type NOT LIKE 'RECONCILE_%' AND action_type NOT LIKE 'REPRICE_%' THEN 1 ELSE 0 END) AS manual_rows
        FROM actions_ledger
        """
    ).fetchone()
    if row is None:
        return {"total_rows": 0, "system_rows": 0, "manual_rows": 0}
    return {
        "total_rows": int(row["total_rows"] or 0),
        "system_rows": int(row["system_rows"] or 0),
        "manual_rows": int(row["manual_rows"] or 0),
    }


def query_action_ledger(
    con,
    *,
    symbol: Optional[str] = None,
    action_type: str = "ALL",
    source: str = "ALL",
    limit: int = 100,
):
    where = []
    params: list[Any] = []

    normalized_symbol = canonical_futures_symbol(symbol) if symbol else None
    if normalized_symbol:
        where.append("UPPER(symbol) = UPPER(?)")
        params.append(normalized_symbol)

    action_type_norm = str(action_type or "ALL").strip().upper()
    if action_type_norm != "ALL":
        where.append("UPPER(action_type) = ?")
        params.append(action_type_norm)

    source_norm = str(source or "ALL").strip().upper()
    if source_norm == "SYSTEM":
        where.append("(action_type LIKE 'QUEUE_%' OR action_type LIKE 'EXECUTOR_%' OR action_type LIKE 'RECONCILE_%' OR action_type LIKE 'REPRICE_%')")
    elif source_norm == "MANUAL":
        where.append("(action_type NOT LIKE 'QUEUE_%' AND action_type NOT LIKE 'EXECUTOR_%' AND action_type NOT LIKE 'RECONCILE_%' AND action_type NOT LIKE 'REPRICE_%')")

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    rows = con.execute(
        f"""
        SELECT id, created_at, symbol, action_type, side, qty, price, note
        FROM actions_ledger
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def parse_action_ledger_note(note: Optional[str]) -> dict[str, str]:
    text = str(note or "").strip()
    if not text:
        return {}

    parsed: dict[str, str] = {}
    for part in text.split("|"):
        token = str(part).strip()
        if not token or "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = str(key).strip()
        value = str(value).strip()
        if key:
            parsed[key] = value
    return parsed


def enrich_action_ledger_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        parsed = parse_action_ledger_note(item.get("note"))
        item["note_action_id"] = parsed.get("action_id")
        item["note_status"] = parsed.get("status")
        item["note_lifecycle_state"] = parsed.get("lifecycle_state")
        item["note_stage"] = parsed.get("stage")
        item["note_reason"] = parsed.get("reason")
        item["note_previous_order_id"] = parsed.get("previous_order_id")
        item["note_new_order_id"] = parsed.get("new_order_id")
        enriched.append(item)
    return enriched


def summarize_action_ledger_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        action_id = str(row.get("note_action_id") or "").strip()
        if not action_id:
            continue

        current = grouped.setdefault(
            action_id,
            {
                "action_id": action_id,
                "symbol": row.get("symbol"),
                "first_at": row.get("created_at"),
                "last_at": row.get("created_at"),
                "events": 0,
                "latest_event_type": row.get("action_type"),
                "latest_status": row.get("note_status"),
                "latest_lifecycle_state": row.get("note_lifecycle_state"),
                "latest_stage": row.get("note_stage"),
                "latest_reason": row.get("note_reason"),
            },
        )

        current["events"] = int(current["events"]) + 1
        current["symbol"] = current.get("symbol") or row.get("symbol")

        created_at = row.get("created_at")
        if created_at and (current.get("first_at") is None or str(created_at) < str(current["first_at"])):
            current["first_at"] = created_at
        if created_at and (current.get("last_at") is None or str(created_at) > str(current["last_at"])):
            current["last_at"] = created_at
            current["latest_event_type"] = row.get("action_type")
            current["latest_status"] = row.get("note_status")
            current["latest_lifecycle_state"] = row.get("note_lifecycle_state")
            current["latest_stage"] = row.get("note_stage")
            current["latest_reason"] = row.get("note_reason")

    return sorted(grouped.values(), key=lambda x: (str(x.get("last_at") or ""), int(x.get("action_id") or 0)), reverse=True)

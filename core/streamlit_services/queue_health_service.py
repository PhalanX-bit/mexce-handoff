from __future__ import annotations

from core.symbol_utils import build_symbol_aliases


def _normalize_symbols(symbols) -> list[str]:
    out = []
    seen = set()

    for symbol in symbols or []:
        aliases = build_symbol_aliases(symbol) or [str(symbol or "").strip()]
        for alias in aliases:
            s = str(alias or "").strip()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)

    return out


def _list_rows_for_symbols(con, *, symbols, where_sql: str, order_sql: str, limit: int = 50):
    normalized = _normalize_symbols(symbols)
    if not normalized:
        return []

    placeholders = ",".join("?" for _ in normalized)
    sql = f"""
        SELECT *
        FROM action_queue
        WHERE UPPER(symbol) IN ({placeholders})
          AND ({where_sql})
        {order_sql}
        LIMIT ?
    """
    rows = con.execute(sql, tuple(normalized) + (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def get_queue_health_summary(con) -> dict:
    out = {
        "armed_count": 0,
        "running_count": 0,
        "error_count": 0,
        "reprice_payload_count": 0,
        "reconcile_open_count": 0,
        "reconcile_not_found_count": 0,
    }

    row = con.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'ARMED' THEN 1 ELSE 0 END) AS armed_count,
            SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) AS running_count,
            SUM(CASE WHEN COALESCE(last_error, '') <> '' THEN 1 ELSE 0 END) AS error_count,
            SUM(CASE WHEN last_reprice_payload IS NOT NULL AND last_reprice_payload <> '' THEN 1 ELSE 0 END) AS reprice_payload_count,
            SUM(CASE WHEN reconcile_state = 'OPEN' THEN 1 ELSE 0 END) AS reconcile_open_count,
            SUM(CASE WHEN reconcile_state = 'NOT_FOUND' THEN 1 ELSE 0 END) AS reconcile_not_found_count
        FROM action_queue
        """
    ).fetchone()

    if row:
        for key in out.keys():
            try:
                out[key] = int(row[key] or 0)
            except Exception:
                out[key] = 0

    return out


def summarize_health_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol"),
        "status": row.get("status"),
        "api_order_id": row.get("api_order_id"),
        "api_mode": row.get("api_mode"),
        "last_error": row.get("last_error"),
        "reconcile_state": row.get("reconcile_state"),
        "has_reprice_payload": bool(row.get("last_reprice_payload")),
    }


def list_running_or_armed_rows(con, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE status IN ('ARMED', 'RUNNING')
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_rows_with_errors(con, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE COALESCE(last_error, '') <> ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_recent_reprice_rows(con, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE api_mode = 'direct_futures_api_replace'
           OR (last_reprice_payload IS NOT NULL AND last_reprice_payload <> '')
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_recent_reconcile_rows(con, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE reconcile_checked_at IS NOT NULL
        ORDER BY reconcile_checked_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_rows_with_reprice_payload(con, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE last_reprice_payload IS NOT NULL
          AND last_reprice_payload <> ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_running_or_armed_rows_for_symbol(con, *, symbol: str, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE symbol = ?
          AND status IN ('ARMED', 'RUNNING')
        ORDER BY id DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_rows_with_errors_for_symbol(con, *, symbol: str, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE symbol = ?
          AND COALESCE(last_error, '') <> ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_recent_reprice_rows_for_symbol(con, *, symbol: str, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE symbol = ?
          AND (
                api_mode = 'direct_futures_api_replace'
                OR (last_reprice_payload IS NOT NULL AND last_reprice_payload <> '')
              )
        ORDER BY id DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_recent_reconcile_rows_for_symbol(con, *, symbol: str, limit: int = 50):
    rows = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE symbol = ?
          AND reconcile_checked_at IS NOT NULL
        ORDER BY reconcile_checked_at DESC, id DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def list_running_or_armed_rows_for_symbols(con, *, symbols, limit: int = 50):
    return _list_rows_for_symbols(
        con,
        symbols=symbols,
        where_sql="status IN ('ARMED', 'RUNNING')",
        order_sql="ORDER BY id DESC",
        limit=limit,
    )


def list_rows_with_errors_for_symbols(con, *, symbols, limit: int = 50):
    return _list_rows_for_symbols(
        con,
        symbols=symbols,
        where_sql="COALESCE(last_error, '') <> ''",
        order_sql="ORDER BY id DESC",
        limit=limit,
    )


def list_recent_reprice_rows_for_symbols(con, *, symbols, limit: int = 50):
    return _list_rows_for_symbols(
        con,
        symbols=symbols,
        where_sql="""
            api_mode = 'direct_futures_api_replace'
            OR (last_reprice_payload IS NOT NULL AND last_reprice_payload <> '')
        """,
        order_sql="ORDER BY id DESC",
        limit=limit,
    )


def list_recent_reconcile_rows_for_symbols(con, *, symbols, limit: int = 50):
    return _list_rows_for_symbols(
        con,
        symbols=symbols,
        where_sql="reconcile_checked_at IS NOT NULL",
        order_sql="ORDER BY reconcile_checked_at DESC, id DESC",
        limit=limit,
    )

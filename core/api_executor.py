from __future__ import annotations

import inspect
import json
import sqlite3
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from core.db import DB_PATH
from core.mexc_direct import (
    create_limit_order_and_get_id,
    create_limit_order_raw,
    create_market_order_raw,
    futures_symbol_raw,
)
from core.streamlit_services.action_ledger_service import (
    build_action_queue_ledger_note,
    log_action_queue_event,
)

API_MODE = "direct_futures_api"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@contextmanager
def db_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"), default=str)


def get_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(action_queue)").fetchall()
    return {r["name"] for r in rows}


def update_action(conn: sqlite3.Connection, action_id: int, **fields: Any) -> None:
    cols = get_columns(conn)
    allowed = {k: v for k, v in fields.items() if k in cols}

    if "last_update_at" in cols and "last_update_at" not in allowed:
        allowed["last_update_at"] = utc_now_iso()

    if not allowed:
        return

    parts = []
    values = []
    for k, v in allowed.items():
        parts.append(f"{k} = ?")
        values.append(v)

    values.append(action_id)
    sql = f"UPDATE action_queue SET {', '.join(parts)} WHERE id = ?"
    conn.execute(sql, values)


def get_action_by_id(conn: sqlite3.Connection, action_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM action_queue WHERE id = ?", (action_id,)).fetchone()
    return row_to_dict(row)


def claim_action(conn: sqlite3.Connection, action_id: int) -> Optional[Dict[str, Any]]:
    cols = get_columns(conn)

    conn.execute("BEGIN IMMEDIATE")

    row = conn.execute(
        """
        SELECT *
        FROM action_queue
        WHERE id = ? AND status = 'ARMED'
        """,
        (action_id,),
    ).fetchone()

    if row is None:
        conn.rollback()
        return None

    attempts = 1
    if "attempts" in cols and row["attempts"] is not None:
        attempts = int(row["attempts"]) + 1

    update_action(
        conn,
        action_id,
        status="RUNNING",
        attempts=attempts,
        last_error=None,
    )
    conn.commit()
    claimed = get_action_by_id(conn, action_id)
    if claimed is not None:
        log_action_queue_event(
            conn,
            created_at=claimed.get("last_update_at") or utc_now_iso(),
            action_id=action_id,
            symbol=claimed.get("symbol"),
            event_type="EXECUTOR_RUNNING",
            side=claimed.get("side"),
            qty=claimed.get("qty"),
            price=claimed.get("limit_price"),
            note=build_action_queue_ledger_note(action_id, "RUNNING"),
        )
        conn.commit()
    return claimed


def normalize_action(action: Dict[str, Any]) -> Dict[str, Any]:
    panel_mode = str(action.get("panel_mode") or "OPEN").upper().strip()
    ui_side = str(action.get("side") or "").upper().strip()
    order_kind = str(action.get("order_kind") or action.get("order_type") or "LIMIT").upper().strip()

    if panel_mode not in {"OPEN", "CLOSE"}:
        raise ValueError(f"Unsupported panel_mode: {panel_mode}")

    if ui_side not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported side: {ui_side}")

    if order_kind in {"LIMIT", "POST_ONLY"}:
        exec_mode = "LIMIT"
    elif order_kind in {"MARKET", "CHASE"}:
        exec_mode = "MARKET"
    else:
        raise ValueError(f"Unsupported order kind: {order_kind}")

    qty = action.get("qty")
    if qty is None:
        raise ValueError("qty is required")
    qty = float(qty)

    limit_price = action.get("limit_price")
    if limit_price is not None:
        limit_price = float(limit_price)

    leverage = action.get("leverage")
    if leverage in (None, ""):
        raise ValueError("Action is missing leverage")
    leverage = int(leverage)
    if leverage <= 0:
        raise ValueError(f"Invalid leverage for action {action.get('id')}: {leverage}")

    symbol = str(action["symbol"]).strip()
    symbol_raw = futures_symbol_raw(symbol)

    reduce_only = bool(action.get("reduce_only")) or panel_mode == "CLOSE"

    if panel_mode == "OPEN" and ui_side == "LONG":
        position_intent = "OPEN_LONG"
    elif panel_mode == "OPEN" and ui_side == "SHORT":
        position_intent = "OPEN_SHORT"
    elif panel_mode == "CLOSE" and ui_side == "LONG":
        position_intent = "CLOSE_LONG"
    elif panel_mode == "CLOSE" and ui_side == "SHORT":
        position_intent = "CLOSE_SHORT"
    else:
        raise ValueError(f"Unsupported panel_mode/side combination: {panel_mode}/{ui_side}")

    client_oid = f"aq-{action['id']}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

    return {
        "id": int(action["id"]),
        "symbol": symbol,
        "symbol_raw": symbol_raw,
        "panel_mode": panel_mode,
        "ui_side": ui_side,
        "api_side": position_intent,
        "position_intent": position_intent,
        "order_kind": order_kind,
        "exec_mode": exec_mode,
        "qty": qty,
        "vol": qty,
        "qty_unit": str(action.get("qty_unit") or "contracts"),
        "limit_price": limit_price,
        "price": limit_price,
        "leverage": leverage,
        "reduce_only": reduce_only,
        "client_oid": client_oid,
        "external_oid": client_oid,
    }


def filter_kwargs_for_callable(func, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    sig = inspect.signature(func)
    accepted = {}
    for name in sig.parameters.keys():
        if name in kwargs:
            accepted[name] = kwargs[name]
    return accepted


def call_flexible(func, kwargs: Dict[str, Any]) -> Any:
    filtered = filter_kwargs_for_callable(func, kwargs)
    return func(**filtered)


def extract_order_id_from_any(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        return str(int(value))

    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return s
        return None

    if isinstance(value, dict):
        for key in ("data", "orderId", "order_id", "id"):
            if key in value:
                nested = extract_order_id_from_any(value[key])
                if nested:
                    return nested
        return None

    if isinstance(value, (list, tuple)):
        for item in value:
            nested = extract_order_id_from_any(item)
            if nested:
                return nested
        return None

    return None


def normalize_submit_result(result: Any) -> Tuple[Optional[str], Any]:
    """
    Нормализира различните възможни форми на return от helper-ите до:
    (api_order_id, raw_response)

    Поддържа случаи като:
    - 795450750506454016
    - {"success": true, "code": 0, "data": 795450750506454016}
    - (raw_response_dict, 795450750506454016)
    - (795450750506454016, raw_response_dict)
    - (raw_response_dict, something_else, "/api/...")
    - (something_else, raw_response_dict, "/api/...")
    """

    if isinstance(result, tuple):
        parts = list(result)

        order_id: Optional[str] = None
        raw_response: Any = result

        for candidate in parts[:2]:
            found = extract_order_id_from_any(candidate)
            if found:
                order_id = found
                break

        if len(parts) >= 2:
            if isinstance(parts[0], dict):
                raw_response = parts[0]
            elif isinstance(parts[1], dict):
                raw_response = parts[1]
            else:
                raw_response = parts[0]
        elif len(parts) == 1:
            raw_response = parts[0]

        if not order_id:
            order_id = extract_order_id_from_any(raw_response)

        return order_id, raw_response

    return extract_order_id_from_any(result), result


def execute_action(normalized: Dict[str, Any]) -> Tuple[Optional[str], Any, str]:
    submit_path = "/api/v1/private/order/submit"

    common_kwargs = {
        "symbol": normalized["symbol"],
        "symbol_raw": normalized["symbol_raw"],
        "side": normalized["api_side"],
        "ui_side": normalized["ui_side"],
        "panel_mode": normalized["panel_mode"],
        "position_intent": normalized["position_intent"],
        "qty": normalized["qty"],
        "vol": normalized["vol"],
        "qty_unit": normalized["qty_unit"],
        "price": normalized["price"],
        "limit_price": normalized["limit_price"],
        "reduce_only": normalized["reduce_only"],
        "leverage": normalized["leverage"],
        "client_oid": normalized["client_oid"],
        "external_oid": normalized["external_oid"],
        "order_kind": normalized["order_kind"],
        "post_only": normalized["order_kind"] == "POST_ONLY",
    }

    if normalized["exec_mode"] == "LIMIT":
        if normalized["limit_price"] is None:
            raise ValueError("limit_price is required for LIMIT order")

        try:
            result = call_flexible(create_limit_order_and_get_id, common_kwargs)
            order_id, raw = normalize_submit_result(result)
            return order_id, raw, submit_path

        except TypeError:
            raw = call_flexible(create_limit_order_raw, common_kwargs)
            order_id, raw = normalize_submit_result(raw)
            return order_id, raw, submit_path

    if normalized["exec_mode"] == "MARKET":
        raw = call_flexible(create_market_order_raw, common_kwargs)
        order_id, raw = normalize_submit_result(raw)
        return order_id, raw, submit_path

    raise ValueError(f"Unsupported exec mode: {normalized['exec_mode']}")


def mark_done(
    conn: sqlite3.Connection,
    action_id: int,
    api_order_id: Optional[str],
    api_client_oid: str,
    api_submit_path: str,
    api_response: Any,
) -> None:
    update_action(
        conn,
        action_id,
        status="DONE",
        api_order_id=api_order_id,
        api_client_oid=api_client_oid,
        api_submit_path=api_submit_path,
        api_mode=API_MODE,
        api_response=safe_json(api_response),
        last_error=None,
    )
    action_row = get_action_by_id(conn, action_id)
    if action_row is not None:
        log_action_queue_event(
            conn,
            created_at=action_row.get("last_update_at") or utc_now_iso(),
            action_id=action_id,
            symbol=action_row.get("symbol"),
            event_type="EXECUTOR_DONE",
            side=action_row.get("side"),
            qty=action_row.get("qty"),
            price=action_row.get("limit_price"),
            note=build_action_queue_ledger_note(
                action_id,
                "DONE",
                f"api_order_id={api_order_id or ''} | api_mode={API_MODE}",
            ),
        )
    conn.commit()


def mark_failed(
    conn: sqlite3.Connection,
    action_id: int,
    api_client_oid: Optional[str],
    api_submit_path: Optional[str],
    api_response: Any,
    error_text: str,
) -> None:
    update_action(
        conn,
        action_id,
        status="FAILED",
        api_client_oid=api_client_oid,
        api_submit_path=api_submit_path,
        api_mode=API_MODE,
        api_response=safe_json(api_response) if api_response is not None else None,
        last_error=error_text[:4000],
    )
    action_row = get_action_by_id(conn, action_id)
    if action_row is not None:
        short_error = str(error_text or "").strip().splitlines()[0][:500]
        log_action_queue_event(
            conn,
            created_at=action_row.get("last_update_at") or utc_now_iso(),
            action_id=action_id,
            symbol=action_row.get("symbol"),
            event_type="EXECUTOR_FAILED",
            side=action_row.get("side"),
            qty=action_row.get("qty"),
            price=action_row.get("limit_price"),
            note=build_action_queue_ledger_note(action_id, "FAILED", short_error),
        )
    conn.commit()


def process_one_action(action_id: int, verbose: bool = True) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        action = claim_action(conn, action_id)
        if action is None:
            if verbose:
                print(f"Action id={action_id} is not ARMED or does not exist.")
            return None

        normalized = None
        raw_response = None
        submit_path = None

        try:
            normalized = normalize_action(action)
            api_order_id, raw_response, submit_path = execute_action(normalized)

            mark_done(
                conn=conn,
                action_id=action_id,
                api_order_id=api_order_id,
                api_client_oid=normalized["client_oid"],
                api_submit_path=submit_path,
                api_response=raw_response,
            )

            final_row = get_action_by_id(conn, action_id)

            if verbose:
                print(
                    json.dumps(
                        {
                            "result": "DONE",
                            "action_id": action_id,
                            "api_order_id": api_order_id,
                            "api_client_oid": normalized["client_oid"],
                            "api_submit_path": submit_path,
                        },
                        ensure_ascii=False,
                    )
                )

            return final_row

        except Exception as exc:
            error_text = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"

            mark_failed(
                conn=conn,
                action_id=action_id,
                api_client_oid=(normalized or {}).get("client_oid"),
                api_submit_path=submit_path,
                api_response=raw_response,
                error_text=error_text,
            )

            final_row = get_action_by_id(conn, action_id)

            if verbose:
                print(
                    json.dumps(
                        {
                            "result": "FAILED",
                            "action_id": action_id,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                )

            return final_row


def print_action_status(action_id: int) -> Optional[Dict[str, Any]]:
    with db_conn() as conn:
        row = get_action_by_id(conn, action_id)
        if row is None:
            print(f"Action id={action_id} not found.")
            return None

        out = {
            "id": row.get("id"),
            "status": row.get("status"),
            "symbol": row.get("symbol"),
            "panel_mode": row.get("panel_mode"),
            "side": row.get("side"),
            "qty": row.get("qty"),
            "limit_price": row.get("limit_price"),
            "order_kind": row.get("order_kind"),
            "order_type": row.get("order_type"),
            "api_order_id": row.get("api_order_id"),
            "api_client_oid": row.get("api_client_oid"),
            "api_submit_path": row.get("api_submit_path"),
            "api_mode": row.get("api_mode"),
            "last_error": row.get("last_error"),
            "last_update_at": row.get("last_update_at"),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return row


if __name__ == "__main__":
    print("Run via imported process_one_action(action_id=...)")

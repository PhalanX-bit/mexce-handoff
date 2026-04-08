from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "mexc.sqlite"

app = FastAPI(title="MEXC Local Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _norm_symbol(symbol: Optional[str]) -> str:
    return str(symbol or "").strip().upper().replace(":USDT", "/USDT")


def _norm_side(side: Optional[str]) -> str:
    return str(side or "").strip().upper()


def _parse_limit_deferred_note(note: Optional[str]) -> Optional[Dict[str, Any]]:
    if not note:
        return None
    if "LIMIT_DEFERRED_OK" not in note:
        return None

    parts = [p.strip() for p in str(note).split("|")]
    out: Dict[str, Any] = {}

    for p in parts:
        if p.startswith("symbol="):
            out["symbol"] = _norm_symbol(p.split("=", 1)[1].strip())
        elif p.startswith("side="):
            out["side"] = _norm_side(p.split("=", 1)[1].strip())
        elif p.startswith("price="):
            try:
                out["limit_price"] = float(p.split("=", 1)[1].strip())
            except Exception:
                return None
        elif p.startswith("qty="):
            try:
                out["qty"] = float(p.split("=", 1)[1].strip())
            except Exception:
                return None

    required = {"symbol", "side", "limit_price", "qty"}
    if not required.issubset(out.keys()):
        return None
    return out


def _upsert_pending_limit_task_from_done_note(
    con: sqlite3.Connection,
    action_id: int,
    note: Optional[str],
) -> None:
    parsed = _parse_limit_deferred_note(note)
    if not parsed:
        return

    con.execute(
        """
        INSERT INTO pending_limit_tasks
        (action_id, symbol, side, limit_price, qty, status, trigger_seen, created_at, updated_at, note)
        VALUES (?, ?, ?, ?, ?, 'PENDING', 0, datetime('now'), datetime('now'), ?)
        ON CONFLICT(action_id) DO UPDATE SET
            symbol=excluded.symbol,
            side=excluded.side,
            limit_price=excluded.limit_price,
            qty=excluded.qty,
            updated_at=datetime('now'),
            note=excluded.note
        """,
        (
            action_id,
            parsed["symbol"],
            parsed["side"],
            parsed["limit_price"],
            parsed["qty"],
            note,
        ),
    )


def _get_latest_position_row(
    con: sqlite3.Connection,
    symbol: str,
    side: str,
) -> Optional[sqlite3.Row]:
    symbol = _norm_symbol(symbol)
    side = _norm_side(side)

    row = con.execute(
        """
        SELECT id, symbol, side, contracts, entry_price, unrealized_pnl, created_at
        FROM positions_snapshot
        WHERE UPPER(REPLACE(symbol, ':USDT', '/USDT')) = ?
          AND UPPER(side) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol, side),
    ).fetchone()

    return row


def _get_latest_position_payload(
    con: sqlite3.Connection,
    symbol: str,
    side: str,
) -> Dict[str, Any]:
    symbol = _norm_symbol(symbol)
    side = _norm_side(side)

    row = _get_latest_position_row(con, symbol, side)
    if not row:
        return {
            "ok": False,
            "error": "position_not_found",
            "symbol": symbol,
            "side": side,
            "contracts": 0.0,
        }

    return {
        "ok": True,
        "symbol": symbol,
        "side": side,
        "contracts": float(row["contracts"] or 0.0),
        "entry_price": float(row["entry_price"] or 0.0),
        "unrealized_pnl": float(row["unrealized_pnl"] or 0.0),
        "created_at": row["created_at"],
        "snapshot_id": int(row["id"]),
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "db": str(DB_PATH)}


@app.get("/queue/next")
def queue_next() -> Dict[str, Any]:
    con = connect()
    try:
        row = con.execute(
            """
            SELECT *
            FROM action_queue
            WHERE status='ARMED'
            ORDER BY priority ASC, id ASC
            LIMIT 1
            """
        ).fetchone()

        if not row:
            return {"action": None}

        return {"action": dict(row)}
    finally:
        con.close()


@app.post("/queue/{action_id}/running")
def queue_running(action_id: int) -> Dict[str, Any]:
    con = connect()
    try:
        cur = con.execute(
            """
            UPDATE action_queue
            SET status='RUNNING',
                attempts=attempts+1,
                last_update_at=datetime('now')
            WHERE id=? AND status='ARMED'
            """,
            (action_id,),
        )
        con.commit()
        return {"updated": cur.rowcount}
    finally:
        con.close()


@app.post("/queue/{action_id}/done")
def queue_done(action_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    note = (payload or {}).get("note")
    con = connect()
    try:
        cur = con.execute(
            """
            UPDATE action_queue
            SET status='DONE',
                last_error=NULL,
                last_update_at=datetime('now'),
                note=COALESCE(?, note)
            WHERE id=? AND status IN ('RUNNING','ARMED')
            """,
            (note, action_id),
        )

        if cur.rowcount > 0:
            _upsert_pending_limit_task_from_done_note(con, action_id, note)

        con.commit()
        return {"updated": cur.rowcount}
    finally:
        con.close()


@app.post("/queue/{action_id}/failed")
def queue_failed(action_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    err = payload.get("error", "unknown error")
    con = connect()
    try:
        cur = con.execute(
            """
            UPDATE action_queue
            SET status='FAILED',
                last_error=?,
                last_update_at=datetime('now')
            WHERE id=? AND status IN ('RUNNING','ARMED')
            """,
            (err, action_id),
        )
        con.commit()
        return {"updated": cur.rowcount}
    finally:
        con.close()


@app.get("/pending_limit_tasks")
def list_pending_limit_tasks(status: Optional[str] = None) -> Dict[str, Any]:
    con = connect()
    try:
        if status:
            rows = con.execute(
                """
                SELECT *
                FROM pending_limit_tasks
                WHERE status=?
                ORDER BY id ASC
                """,
                (status,),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT *
                FROM pending_limit_tasks
                ORDER BY id ASC
                """
            ).fetchall()

        return {"items": [dict(r) for r in rows]}
    finally:
        con.close()


@app.get("/positions/latest")
def positions_latest(symbol: str, side: str) -> Dict[str, Any]:
    con = connect()
    try:
        return _get_latest_position_payload(con, symbol, side)
    finally:
        con.close()


@app.get("/positions/contracts")
def positions_contracts(symbol: str, side: str) -> Dict[str, Any]:
    con = connect()
    try:
        payload = _get_latest_position_payload(con, symbol, side)
        return {
            "ok": payload["ok"],
            "error": payload.get("error"),
            "symbol": payload["symbol"],
            "side": payload["side"],
            "contracts": payload["contracts"],
            "created_at": payload.get("created_at"),
            "snapshot_id": payload.get("snapshot_id"),
        }
    finally:
        con.close()


@app.get("/positions/changed")
def positions_changed(symbol: str, side: str, after_contracts: float) -> Dict[str, Any]:
    con = connect()
    try:
        payload = _get_latest_position_payload(con, symbol, side)

        if not payload["ok"]:
            return {
                "ok": False,
                "error": payload.get("error", "position_not_found"),
                "symbol": payload["symbol"],
                "side": payload["side"],
                "before_contracts": float(after_contracts),
                "now_contracts": 0.0,
                "changed": False,
            }

        now_contracts = float(payload["contracts"] or 0.0)

        return {
            "ok": True,
            "symbol": payload["symbol"],
            "side": payload["side"],
            "before_contracts": float(after_contracts),
            "now_contracts": now_contracts,
            "changed": now_contracts > float(after_contracts),
            "created_at": payload.get("created_at"),
            "snapshot_id": payload.get("snapshot_id"),
        }
    finally:
        con.close()
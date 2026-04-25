from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "mexc.sqlite"

app = FastAPI(title="MEXC Local Bridge")

# Tampermonkey (GM_xmlhttpRequest) will call localhost. CORS is fine to allow.
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

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "db": str(DB_PATH)}

@app.get("/queue/next")
def queue_next() -> Dict[str, Any]:
    """
    Return the next ARMED action (highest priority, lowest id).
    """
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

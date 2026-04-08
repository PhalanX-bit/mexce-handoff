from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = Path("data/mexc.sqlite")

app = FastAPI(title="MEXC Local Bridge", version="0.2")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


class DoneBody(BaseModel):
    note: Optional[str] = None


class FailBody(BaseModel):
    error: str


@app.get("/queue/next")
def queue_next() -> Dict[str, Any]:
    con = connect()
    row = con.execute(
        """
        SELECT *
        FROM action_queue
        WHERE status='ARMED'
        ORDER BY priority ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    con.close()
    return {"ok": True, "action": dict(row) if row else None}


@app.post("/queue/{action_id}/running")
def mark_running(action_id: int):
    con = connect()
    cur = con.execute(
        """
        UPDATE action_queue
        SET status='RUNNING', attempts=attempts+1, last_update_at=?
        WHERE id=? AND status='ARMED'
        """,
        (now_utc_iso(), action_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Action not in ARMED state.")
    return {"ok": True}


@app.post("/queue/{action_id}/done")
def mark_done(action_id: int, body: DoneBody):
    con = connect()
    cur = con.execute(
        """
        UPDATE action_queue
        SET status='DONE', last_error=NULL, last_update_at=?,
            note=TRIM(COALESCE(note,'') || CASE WHEN ? IS NULL OR ?='' THEN '' ELSE ('\nDONE: ' || ?) END)
        WHERE id=? AND status='RUNNING'
        """,
        (now_utc_iso(), body.note, body.note, body.note, action_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Action not in RUNNING state.")
    return {"ok": True}


@app.post("/queue/{action_id}/failed")
def mark_failed(action_id: int, body: FailBody):
    con = connect()
    cur = con.execute(
        """
        UPDATE action_queue
        SET status='FAILED', last_error=?, last_update_at=?
        WHERE id=? AND status='RUNNING'
        """,
        (body.error, now_utc_iso(), action_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Action not in RUNNING state.")
    return {"ok": True}


@app.post("/queue/{action_id}/cancel")
def cancel(action_id: int):
    con = connect()
    cur = con.execute(
        """
        UPDATE action_queue
        SET status='CANCELED', last_update_at=?
        WHERE id=? AND status IN ('PENDING','ARMED')
        """,
        (now_utc_iso(), action_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Action not in PENDING/ARMED state.")
    return {"ok": True}


@app.post("/queue/{action_id}/arm")
def arm(action_id: int):
    con = connect()
    cur = con.execute(
        """
        UPDATE action_queue
        SET status='ARMED', last_update_at=?
        WHERE id=? AND status='PENDING'
        """,
        (now_utc_iso(), action_id),
    )
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Action not in PENDING state.")
    return {"ok": True}

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


UTC = timezone.utc


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class LockAcquireResult:
    acquired: bool
    lock_token: str | None
    reason: str


def build_lock_token() -> str:
    return secrets.token_hex(16)


def acquire_action_lock(
    conn: sqlite3.Connection,
    action_id: int,
    *,
    lock_timeout_sec: int = 90,
    table_name: str = "action_queue",
) -> LockAcquireResult:
    """
    Minimal per-action lock.

    Rules:
    - if ops_lock_token is null/empty -> acquire
    - if ops_locked_at is older than lock_timeout_sec -> steal expired lock
    - otherwise -> fail acquire
    """
    row = conn.execute(
        f"""
        SELECT ops_lock_token, ops_locked_at
        FROM {table_name}
        WHERE id = ?
        """,
        (action_id,),
    ).fetchone()

    if row is None:
        return LockAcquireResult(
            acquired=False,
            lock_token=None,
            reason="action_not_found",
        )

    existing_token = row[0]
    existing_locked_at = row[1]

    now = datetime.now(UTC)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    new_token = build_lock_token()

    should_acquire = False

    if existing_token is None or str(existing_token).strip() == "":
        should_acquire = True
    else:
        locked_at_dt = parse_utc_iso(existing_locked_at)
        if locked_at_dt is None:
            should_acquire = True
        else:
            is_expired = (now - locked_at_dt) > timedelta(seconds=lock_timeout_sec)
            if is_expired:
                should_acquire = True

    if not should_acquire:
        return LockAcquireResult(
            acquired=False,
            lock_token=None,
            reason="locked",
        )

    cur = conn.execute(
        f"""
        UPDATE {table_name}
        SET
            ops_lock_token = ?,
            ops_locked_at = ?
        WHERE id = ?
          AND (
                ops_lock_token IS NULL
                OR TRIM(ops_lock_token) = ''
                OR ops_locked_at IS NULL
                OR ops_locked_at = ''
                OR ops_locked_at < ?
          )
        """,
        (
            new_token,
            now_iso,
            action_id,
            (
                now - timedelta(seconds=lock_timeout_sec)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        ),
    )
    conn.commit()

    if cur.rowcount == 1:
        return LockAcquireResult(
            acquired=True,
            lock_token=new_token,
            reason="acquired",
        )

    return LockAcquireResult(
        acquired=False,
        lock_token=None,
        reason="locked_race",
    )


def release_action_lock(
    conn: sqlite3.Connection,
    action_id: int,
    lock_token: str,
    *,
    table_name: str = "action_queue",
) -> bool:
    """
    Release only if the current row still holds our token.
    """
    cur = conn.execute(
        f"""
        UPDATE {table_name}
        SET
            ops_lock_token = NULL,
            ops_locked_at = NULL
        WHERE id = ?
          AND ops_lock_token = ?
        """,
        (action_id, lock_token),
    )
    conn.commit()
    return cur.rowcount == 1
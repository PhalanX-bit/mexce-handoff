from __future__ import annotations

import sqlite3

from mexce.core.lean_hardening_schema import ensure_action_queue_lean_hardening_columns
from mexce.core.ops_lock import acquire_action_lock, release_action_lock


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE action_queue (
            id INTEGER PRIMARY KEY,
            last_error TEXT,
            api_order_id TEXT
        )
        """
    )
    ensure_action_queue_lean_hardening_columns(conn)
    conn.execute(
        """
        INSERT INTO action_queue (id, last_error, api_order_id)
        VALUES (1, NULL, 'OID-1')
        """
    )
    conn.commit()
    return conn


def test_acquire_then_block_then_release():
    conn = _make_conn()

    first = acquire_action_lock(conn, 1, lock_timeout_sec=90)
    assert first.acquired is True
    assert first.lock_token

    second = acquire_action_lock(conn, 1, lock_timeout_sec=90)
    assert second.acquired is False

    released = release_action_lock(conn, 1, first.lock_token)
    assert released is True

    third = acquire_action_lock(conn, 1, lock_timeout_sec=90)
    assert third.acquired is True
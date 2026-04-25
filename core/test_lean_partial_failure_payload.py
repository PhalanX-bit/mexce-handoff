from __future__ import annotations

import json
import sqlite3

from mexce.core.lean_hardening_schema import ensure_action_queue_lean_hardening_columns
import mexce.core.reprice_action_once_hardened as hardened


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE action_queue (
            id INTEGER PRIMARY KEY,
            api_order_id TEXT,
            last_error TEXT
        )
        """
    )
    ensure_action_queue_lean_hardening_columns(conn)
    conn.execute(
        """
        INSERT INTO action_queue (id, api_order_id, last_error)
        VALUES (1, 'OLD-ORDER-1', NULL)
        """
    )
    conn.commit()
    return conn


def test_error_persists_last_error_and_payload(monkeypatch):
    conn = _make_conn()

    def fake_evaluate_reprice_need(**kwargs):
        return {
            "current_price": 100.0,
            "drift_bps": 15.0,
            "overshoot_kind": "adverse_up",
            "effective_threshold_bps": 10.0,
            "order_age_sec": 5.0,
            "max_order_age_sec": None,
            "stale_by_drift": True,
            "stale_by_age": False,
            "should_replace": True,
            "decision_reason": "stale_by_drift",
        }

    def fake_replace_limit_order_from_action(**kwargs):
        raise RuntimeError("submit failed after cancel")

    monkeypatch.setattr(
        hardened,
        "evaluate_reprice_need",
        fake_evaluate_reprice_need,
    )
    monkeypatch.setattr(
        hardened,
        "replace_limit_order_from_action",
        fake_replace_limit_order_from_action,
    )

    result = hardened.reprice_action_once_hardened(
        conn,
        1,
        target_price=99.5,
        apply_changes=True,
    )
    assert result["stage"] == "error"

    row = conn.execute(
        """
        SELECT last_error, last_reprice_payload
        FROM action_queue
        WHERE id = 1
        """
    ).fetchone()

    assert row is not None
    assert "replace flow error" in (row[0] or "")

    payload = json.loads(row[1])
    assert payload["action_id"] == 1
    assert payload["old_api_order_id"] == "OLD-ORDER-1"
    assert payload["replace_result"] == "error"
    assert payload["submit_error"]["type"] == "RuntimeError"
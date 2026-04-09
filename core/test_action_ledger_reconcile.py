from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.streamlit_services.action_ledger_service import (
    build_reconcile_ledger_note,
    get_action_ledger_summary,
    log_reconcile_event,
    query_action_ledger,
)


def main() -> None:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE actions_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            symbol TEXT,
            action_type TEXT,
            side TEXT,
            qty REAL,
            price REAL,
            note TEXT
        )
        """
    )

    note = build_reconcile_ledger_note(
        254,
        "NOT_FOUND",
        deal_qty=0.0,
        resolved_avg_price=None,
        extra="reason=not_visible_in_available_sources",
    )
    inserted = log_reconcile_event(
        con,
        created_at="2026-04-09T20:00:00.000000Z",
        action_id=254,
        symbol="ADA/USDT",
        lifecycle_state="NOT_FOUND",
        side="SHORT",
        qty=1.0,
        price=0.2427,
        note=note,
    )
    assert inserted is True

    duplicate = log_reconcile_event(
        con,
        created_at="2026-04-09T20:01:00.000000Z",
        action_id=254,
        symbol="ADA/USDT:USDT",
        lifecycle_state="NOT_FOUND",
        side="SHORT",
        qty=1.0,
        price=0.2427,
        note=note,
    )
    assert duplicate is False

    summary = get_action_ledger_summary(con)
    assert summary["total_rows"] == 1
    assert summary["system_rows"] == 1
    assert summary["manual_rows"] == 0

    rows = query_action_ledger(con, source="SYSTEM", limit=20)
    assert len(rows) == 1
    assert rows[0]["action_type"] == "RECONCILE_NOT_FOUND"
    assert rows[0]["symbol"] == "ADA/USDT:USDT"
    assert "action_id=254" in str(rows[0]["note"])

    print("OK: reconcile ledger events are stored as system rows and deduplicated per action/state.")


if __name__ == "__main__":
    main()

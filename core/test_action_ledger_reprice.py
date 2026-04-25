from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.streamlit_services.action_ledger_service import (
    build_reprice_ledger_note,
    get_action_ledger_summary,
    log_reprice_event,
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

    note_one = build_reprice_ledger_note(
        250,
        "REPLACED",
        current_order_price=70000.0,
        target_price=69950.0,
        drift_bps=7.14,
        previous_order_id="111",
        new_order_id="222",
        extra="reason=stale_by_drift",
    )
    inserted_one = log_reprice_event(
        con,
        created_at="2026-04-09T20:10:00.000000Z",
        action_id=250,
        symbol="BTC/USDT",
        stage="REPLACED",
        side="SHORT",
        qty=1.0,
        price=69950.0,
        note=note_one,
    )
    assert inserted_one is True

    duplicate_same_note = log_reprice_event(
        con,
        created_at="2026-04-09T20:11:00.000000Z",
        action_id=250,
        symbol="BTC/USDT:USDT",
        stage="REPLACED",
        side="SHORT",
        qty=1.0,
        price=69950.0,
        note=note_one,
    )
    assert duplicate_same_note is False

    note_two = build_reprice_ledger_note(
        250,
        "REPLACED",
        current_order_price=69950.0,
        target_price=69900.0,
        drift_bps=7.15,
        previous_order_id="222",
        new_order_id="333",
        extra="reason=stale_by_drift",
    )
    inserted_two = log_reprice_event(
        con,
        created_at="2026-04-09T20:12:00.000000Z",
        action_id=250,
        symbol="BTCUSDT",
        stage="REPLACED",
        side="SHORT",
        qty=1.0,
        price=69900.0,
        note=note_two,
    )
    assert inserted_two is True

    summary = get_action_ledger_summary(con)
    assert summary["total_rows"] == 2
    assert summary["system_rows"] == 2
    assert summary["manual_rows"] == 0

    rows = query_action_ledger(con, source="SYSTEM", action_type="REPRICE_REPLACED", limit=20)
    assert len(rows) == 2
    assert all(r["symbol"] == "BTC/USDT:USDT" for r in rows)

    print("OK: reprice ledger events allow distinct replacements and dedupe exact duplicates.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.fill_registry import register_close_fill_from_action, register_open_fill_from_action
from core.streamlit_services.action_ledger_service import get_action_ledger_summary, query_action_ledger


def main() -> None:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE position_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            qty_opened REAL,
            qty_remaining REAL,
            entry_price REAL,
            target_roi_pct REAL,
            leverage REAL,
            target_price REAL,
            opened_at TEXT,
            source_action_id INTEGER,
            source_task_type TEXT,
            source_task_id INTEGER,
            status TEXT
        );

        CREATE TABLE lot_realizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER,
            symbol TEXT,
            side TEXT,
            close_qty REAL,
            entry_price REAL,
            close_price REAL,
            target_price REAL,
            realized_roi_pct REAL,
            closed_at TEXT,
            close_action_id INTEGER,
            close_task_type TEXT,
            close_task_id INTEGER,
            note TEXT
        );

        CREATE TABLE actions_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            symbol TEXT,
            action_type TEXT,
            side TEXT,
            qty REAL,
            price REAL,
            note TEXT
        );
        """
    )

    open_result = register_open_fill_from_action(
        con,
        action_id=254,
        symbol="ADA/USDT",
        side="SHORT",
        qty_opened=1.0,
        entry_price=0.2427,
        target_roi_pct=200.0,
        leverage=300.0,
        opened_at="2026-04-07T13:37:20+00:00",
        source_task_type="ACTION_QUEUE",
        source_task_id=254,
    )
    assert open_result["duplicate"] is False

    open_duplicate = register_open_fill_from_action(
        con,
        action_id=254,
        symbol="ADA/USDT:USDT",
        side="SHORT",
        qty_opened=1.0,
        entry_price=0.2427,
        target_roi_pct=200.0,
        leverage=300.0,
        opened_at="2026-04-07T13:37:20+00:00",
        source_task_type="ACTION_QUEUE",
        source_task_id=254,
    )
    assert open_duplicate["duplicate"] is True

    close_result = register_close_fill_from_action(
        con,
        action_id=300,
        symbol="ADA_USDT",
        side="SHORT",
        close_qty=1.0,
        close_price=0.2410,
        closed_at="2026-04-09T20:30:00.000000Z",
        close_task_type="ACTION_QUEUE",
        close_task_id=300,
        note="test close ledger",
        eligible_first=True,
    )
    assert close_result["duplicate"] is False
    assert abs(float(close_result["matched_close_qty"]) - 1.0) < 1e-9

    summary = get_action_ledger_summary(con)
    assert summary["total_rows"] == 3
    assert summary["system_rows"] == 3

    lot_rows = query_action_ledger(con, source="SYSTEM", limit=20)
    action_types = [str(r["action_type"]) for r in lot_rows]
    assert "LOT_OPEN_REGISTERED" in action_types
    assert "LOT_OPEN_REGISTERED_DUPLICATE" in action_types
    assert "LOT_CLOSE_REGISTERED" in action_types

    print("OK: lot open/close registration logs timeline events into action_ledger.")


if __name__ == "__main__":
    main()

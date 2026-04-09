from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.lots import create_position_lot
from core.streamlit_services.lots_service import preview_close_backfill_from_action_queue


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

        CREATE TABLE action_queue (
            id INTEGER PRIMARY KEY,
            created_at TEXT,
            symbol TEXT,
            panel_mode TEXT,
            side TEXT,
            qty REAL,
            limit_price REAL,
            leverage REAL,
            api_order_id TEXT,
            status TEXT,
            note TEXT
        );
        """
    )

    create_position_lot(
        con,
        symbol="ADA/USDT:USDT",
        side="SHORT",
        qty_opened=1.0,
        entry_price=0.2416,
        target_roi_pct=200.0,
        leverage=300.0,
        opened_at="2026-04-01T18:03:18+00:00",
        source_action_id=188,
        source_task_type="ACTION_QUEUE",
        source_task_id=188,
    )
    create_position_lot(
        con,
        symbol="ADA/USDT:USDT",
        side="SHORT",
        qty_opened=1.0,
        entry_price=0.2427,
        target_roi_pct=200.0,
        leverage=300.0,
        opened_at="2026-04-07T13:37:20+00:00",
        source_action_id=254,
        source_task_type="ACTION_QUEUE",
        source_task_id=254,
    )

    con.execute(
        """
        INSERT INTO action_queue (id, created_at, symbol, panel_mode, side, qty, limit_price, leverage, api_order_id, status, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            300,
            "2026-04-09T20:20:00.000000Z",
            "ADA/USDT:USDT",
            "CLOSE",
            "SHORT",
            1.5,
            0.2409,
            300.0,
            "abc",
            "DONE",
            "manual close preview",
        ),
    )

    preview = preview_close_backfill_from_action_queue(
        con,
        action_id=300,
        eligible_first=True,
    )

    assert preview["panel_mode"] == "CLOSE"
    assert abs(float(preview["requested_close_qty"]) - 1.5) < 1e-9
    assert abs(float(preview["matched_close_qty"]) - 1.5) < 1e-9
    assert abs(float(preview["unmatched_close_qty"]) - 0.0) < 1e-9
    assert len(preview["preview_rows"]) == 2
    assert all(str(r["side"]).upper() == "SHORT" for r in preview["preview_rows"])

    print("OK: close backfill preview matches open lots and reports matched/unmatched qty.")


if __name__ == "__main__":
    main()

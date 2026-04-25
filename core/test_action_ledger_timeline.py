from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.streamlit_services.action_ledger_service import (
    enrich_action_ledger_rows,
    summarize_action_ledger_timeline,
)


def main() -> None:
    rows = [
        {
            "id": 1,
            "created_at": "2026-04-09T20:00:00.000000Z",
            "symbol": "ADA/USDT:USDT",
            "action_type": "QUEUE_CREATED",
            "side": "SHORT",
            "qty": 1.0,
            "price": 0.2427,
            "note": "action_id=254 | status=PENDING",
        },
        {
            "id": 2,
            "created_at": "2026-04-09T20:01:00.000000Z",
            "symbol": "ADA/USDT:USDT",
            "action_type": "EXECUTOR_DONE",
            "side": "SHORT",
            "qty": 1.0,
            "price": 0.2427,
            "note": "action_id=254 | status=DONE",
        },
        {
            "id": 3,
            "created_at": "2026-04-09T20:02:00.000000Z",
            "symbol": "ADA/USDT:USDT",
            "action_type": "RECONCILE_NOT_FOUND",
            "side": "SHORT",
            "qty": 1.0,
            "price": 0.2427,
            "note": "action_id=254 | lifecycle_state=NOT_FOUND | reason=not_visible_in_available_sources",
        },
        {
            "id": 4,
            "created_at": "2026-04-09T20:03:00.000000Z",
            "symbol": "BTC/USDT:USDT",
            "action_type": "REPRICE_REPLACED",
            "side": "SHORT",
            "qty": 1.0,
            "price": 69950.0,
            "note": "action_id=250 | stage=REPLACED | previous_order_id=111 | new_order_id=222 | reason=stale_by_drift",
        },
    ]

    enriched = enrich_action_ledger_rows(rows)
    assert enriched[0]["note_action_id"] == "254"
    assert enriched[2]["note_lifecycle_state"] == "NOT_FOUND"
    assert enriched[3]["note_new_order_id"] == "222"

    timeline = summarize_action_ledger_timeline(enriched)
    assert len(timeline) == 2

    first = timeline[0]
    second = timeline[1]

    assert first["action_id"] == "250"
    assert first["latest_stage"] == "REPLACED"
    assert first["latest_event_type"] == "REPRICE_REPLACED"

    assert second["action_id"] == "254"
    assert second["events"] == 3
    assert second["latest_lifecycle_state"] == "NOT_FOUND"
    assert second["latest_reason"] == "not_visible_in_available_sources"

    print("OK: action ledger timeline parsing groups and summarizes action lifecycle events.")


if __name__ == "__main__":
    main()

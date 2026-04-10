from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.reconcile_service import apply_reconcile_transition_context


def main() -> None:
    action_row = {
        "id": 258,
        "symbol": "ADA/USDT:USDT",
        "reconcile_state": "OPEN",
    }
    result = {
        "lifecycle_state": "NOT_FOUND",
        "lifecycle_reason": "not_visible_in_available_sources",
    }

    adjusted = apply_reconcile_transition_context(action_row, result)
    assert adjusted["previous_reconcile_state"] == "OPEN"
    assert adjusted["lifecycle_state"] == "OPEN_THEN_NOT_FOUND"
    assert adjusted["lifecycle_reason"] == "previously_seen_open_now_not_visible"
    assert adjusted["manual_backfill_hint"] == "likely_fill_or_external_close_check_lot_backfill"

    action_row_fill = {
        "id": 259,
        "symbol": "ADA/USDT:USDT",
        "reconcile_state": "FILLED_CONFIRMED",
    }
    adjusted_fill = apply_reconcile_transition_context(action_row_fill, result)
    assert adjusted_fill["lifecycle_state"] == "FILL_SIGNAL_THEN_NOT_FOUND"
    assert adjusted_fill["manual_backfill_hint"] == "strong_manual_backfill_candidate"

    print("OK: reconcile transition context upgrades not-found states after prior open/fill signals.")


if __name__ == "__main__":
    main()

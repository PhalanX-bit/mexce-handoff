from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.reconcile_service import infer_reconcile_transition


def main() -> None:
    state_one = infer_reconcile_transition(
        current_state="NOT_FOUND",
        previous_state="OPEN",
        prior_states=[],
    )
    assert state_one == (
        "OPEN_THEN_NOT_FOUND",
        "previously_seen_open_now_not_visible",
        "likely_fill_or_external_close_check_lot_backfill",
    )

    state_two = infer_reconcile_transition(
        current_state="NOT_FOUND",
        previous_state="NOT_FOUND",
        prior_states=["OPEN", "NOT_FOUND"],
    )
    assert state_two == (
        "OPEN_THEN_NOT_FOUND",
        "previously_seen_open_now_not_visible",
        "likely_fill_or_external_close_check_lot_backfill",
    )

    state_three = infer_reconcile_transition(
        current_state="NOT_FOUND",
        previous_state="NOT_FOUND",
        prior_states=["FILLED_CONFIRMED", "NOT_FOUND"],
    )
    assert state_three == (
        "FILL_SIGNAL_THEN_NOT_FOUND",
        "previous_fill_signal_now_not_visible",
        "strong_manual_backfill_candidate",
    )

    print("OK: reconcile transition inference upgrades not-found states after prior open/fill signals.")


if __name__ == "__main__":
    main()

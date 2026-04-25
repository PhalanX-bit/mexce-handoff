from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.streamlit_services.strategy_engine as se


def _run_case(*, side: str):
    original = {
        "get_latest_account_snapshot": se.get_latest_account_snapshot,
        "get_latest_positions_per_symbol_side": se.get_latest_positions_per_symbol_side,
        "get_live_ticker_snapshot": se.get_live_ticker_snapshot,
        "get_open_lots_for_symbol": se.get_open_lots_for_symbol,
        "has_active_task_for_symbol": se.has_active_task_for_symbol,
        "has_active_pending_limit_for_symbol": se.has_active_pending_limit_for_symbol,
        "has_active_pending_chase_for_symbol": se.has_active_pending_chase_for_symbol,
        "futures_symbol_raw": se.futures_symbol_raw,
    }

    try:
        se.get_latest_account_snapshot = lambda con: {"equity": 100.0, "created_at": "2026-04-10T00:00:00Z"}
        if side == "LONG":
            se.get_latest_positions_per_symbol_side = lambda con: (
                "2026-04-10T00:00:00Z",
                [{"symbol": "ADA/USDT:USDT", "side": "LONG", "contracts": 2.0, "entry_price": 1.0, "unrealized_pnl": 1.0}],
            )
            se.get_live_ticker_snapshot = lambda symbol: {"last_price": 1.03}
            se.get_open_lots_for_symbol = lambda con, symbol: [
                {
                    "id": 101,
                    "symbol": "ADA/USDT:USDT",
                    "side": "LONG",
                    "qty_remaining": 2.0,
                    "entry_price": 1.0,
                    "target_price": 1.02,
                    "status": "OPEN",
                }
            ]
        else:
            se.get_latest_positions_per_symbol_side = lambda con: (
                "2026-04-10T00:00:00Z",
                [{"symbol": "ADA/USDT:USDT", "side": "SHORT", "contracts": 2.0, "entry_price": 1.0, "unrealized_pnl": 1.0}],
            )
            se.get_live_ticker_snapshot = lambda symbol: {"last_price": 0.97}
            se.get_open_lots_for_symbol = lambda con, symbol: [
                {
                    "id": 202,
                    "symbol": "ADA/USDT:USDT",
                    "side": "SHORT",
                    "qty_remaining": 2.0,
                    "entry_price": 1.0,
                    "target_price": 0.98,
                    "status": "OPEN",
                }
            ]

        se.has_active_task_for_symbol = lambda con, symbol: False
        se.has_active_pending_limit_for_symbol = lambda con, symbol: False
        se.has_active_pending_chase_for_symbol = lambda con, symbol: False
        se.futures_symbol_raw = lambda symbol: "ADA_USDT"

        result = se.evaluate_strategy_state(
            con=None,
            symbol="ADA/USDT:USDT",
            initial_side=side,
            starting_capital=100.0,
            secured_capital=0.0,
            leverage=100.0,
            hedge_loss_usdt=-8.0,
            target_roi_pct=200.0,
            limit_offset_pct=0.05,
            open_limit_offset_pct=0.05,
            rebalance_trigger_pct=0.10,
            moderate_imbalance_ratio=1.5,
            extreme_imbalance_ratio=3.0,
            safe_mode_one_contract=True,
            contract_step=1.0,
            cooldown_sec_override=0.0,
            last_action_ts=0.0,
            gross_cap_contracts=20.0,
        )
        return result
    finally:
        for name, value in original.items():
            setattr(se, name, value)


def _run_leg_target_case(*, side: str):
    original = {
        "get_latest_account_snapshot": se.get_latest_account_snapshot,
        "get_latest_positions_per_symbol_side": se.get_latest_positions_per_symbol_side,
        "get_live_ticker_snapshot": se.get_live_ticker_snapshot,
        "get_open_lots_for_symbol": se.get_open_lots_for_symbol,
        "has_active_task_for_symbol": se.has_active_task_for_symbol,
        "has_active_pending_limit_for_symbol": se.has_active_pending_limit_for_symbol,
        "has_active_pending_chase_for_symbol": se.has_active_pending_chase_for_symbol,
        "futures_symbol_raw": se.futures_symbol_raw,
    }

    try:
        se.get_latest_account_snapshot = lambda con: {"equity": 100.0, "created_at": "2026-04-10T00:00:00Z"}
        if side == "LONG":
            se.get_latest_positions_per_symbol_side = lambda con: (
                "2026-04-10T00:00:00Z",
                [{"symbol": "ADA/USDT:USDT", "side": "LONG", "contracts": 2.0, "entry_price": 1.0, "unrealized_pnl": 1.0}],
            )
            se.get_live_ticker_snapshot = lambda symbol: {"last_price": 1.03}
        else:
            se.get_latest_positions_per_symbol_side = lambda con: (
                "2026-04-10T00:00:00Z",
                [{"symbol": "ADA/USDT:USDT", "side": "SHORT", "contracts": 2.0, "entry_price": 1.0, "unrealized_pnl": 1.0}],
            )
            se.get_live_ticker_snapshot = lambda symbol: {"last_price": 0.97}

        se.get_open_lots_for_symbol = lambda con, symbol: []
        se.has_active_task_for_symbol = lambda con, symbol: False
        se.has_active_pending_limit_for_symbol = lambda con, symbol: False
        se.has_active_pending_chase_for_symbol = lambda con, symbol: False
        se.futures_symbol_raw = lambda symbol: "ADA_USDT"

        return se.evaluate_strategy_state(
            con=None,
            symbol="ADA/USDT:USDT",
            initial_side=side,
            starting_capital=100.0,
            secured_capital=0.0,
            leverage=100.0,
            hedge_loss_usdt=-8.0,
            target_roi_pct=200.0,
            limit_offset_pct=0.05,
            open_limit_offset_pct=0.05,
            rebalance_trigger_pct=0.10,
            moderate_imbalance_ratio=1.5,
            extreme_imbalance_ratio=3.0,
            safe_mode_one_contract=True,
            contract_step=1.0,
            cooldown_sec_override=0.0,
            last_action_ts=0.0,
            gross_cap_contracts=20.0,
        )
    finally:
        for name, value in original.items():
            setattr(se, name, value)


def main():
    long_result = _run_case(side="LONG")
    assert long_result["decision"], "expected a LONG close decision"
    assert long_result["decision"][0] == "CLOSE_LIMIT", long_result["decision"]
    assert long_result["decision"][1] == "LONG", long_result["decision"]
    assert str(long_result["strategy_state"]).startswith("ONE_SIDED_LONG_"), long_result["strategy_state"]
    assert "eligible_lot_ids=101" in str(long_result["decision"][4]), long_result["decision"][4]

    short_result = _run_case(side="SHORT")
    assert short_result["decision"], "expected a SHORT close decision"
    assert short_result["decision"][0] == "CLOSE_LIMIT", short_result["decision"]
    assert short_result["decision"][1] == "SHORT", short_result["decision"]
    assert str(short_result["strategy_state"]).startswith("ONE_SIDED_SHORT_"), short_result["strategy_state"]
    assert "eligible_lot_ids=202" in str(short_result["decision"][4]), short_result["decision"][4]

    long_leg_result = _run_leg_target_case(side="LONG")
    assert long_leg_result["decision"], "expected a LONG leg-target close decision"
    assert long_leg_result["decision"][0] == "CLOSE_LIMIT", long_leg_result["decision"]
    assert "one-sided leg-target" in str(long_leg_result["decision"][4]), long_leg_result["decision"][4]

    short_leg_result = _run_leg_target_case(side="SHORT")
    assert short_leg_result["decision"], "expected a SHORT leg-target close decision"
    assert short_leg_result["decision"][0] == "CLOSE_LIMIT", short_leg_result["decision"]
    assert "one-sided leg-target" in str(short_leg_result["decision"][4]), short_leg_result["decision"][4]

    print("OK: one-sided strategy states can produce CLOSE_LIMIT trim decisions from eligible lots and leg targets.")


if __name__ == "__main__":
    main()

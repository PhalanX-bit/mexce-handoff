from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from core.close_classifier import classify_close_action
from core.open_classifier import classify_open_action
from core.streamlit_services.strategy_service import (
    build_open_limit_price,
    compute_imbalance_ratio,
    compute_strategy_sizing,
    get_strategy_regime_params,
    has_valid_rebalance_signal,
)


@dataclass
class SimLot:
    side: str
    qty_remaining: float
    entry_price: float
    leverage: float
    target_roi_pct: float
    target_price: float


def _normalize_side(side: str) -> str:
    value = str(side or "").strip().upper()
    if value not in {"LONG", "SHORT"}:
        raise ValueError(f"Invalid side: {side}")
    return value


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _compute_leg_upnl(side: str, qty: float, entry_price: float, last_price: float) -> float:
    if qty <= 0 or entry_price <= 0 or last_price <= 0:
        return 0.0

    side = _normalize_side(side)
    if side == "LONG":
        return (last_price - entry_price) * qty
    return (entry_price - last_price) * qty


def _weighted_entry(old_qty: float, old_entry: float, add_qty: float, add_price: float) -> float:
    total_qty = float(old_qty) + float(add_qty)
    if total_qty <= 0:
        return 0.0
    numerator = (float(old_qty) * float(old_entry)) + (float(add_qty) * float(add_price))
    return numerator / total_qty


def _target_price_for_lot(side: str, entry_price: float, leverage: float, target_roi_pct: float) -> float:
    side = _normalize_side(side)
    if entry_price <= 0 or leverage <= 0:
        return 0.0

    target_move_pct = float(target_roi_pct) / float(leverage) / 100.0
    if side == "LONG":
        return float(entry_price) * (1.0 + target_move_pct)
    return float(entry_price) * (1.0 - target_move_pct)


def _compute_lot_target_ready(lot: SimLot, last_price: float) -> bool:
    if lot.qty_remaining <= 0 or lot.entry_price <= 0 or last_price <= 0:
        return False

    if lot.side == "LONG":
        return last_price >= lot.target_price
    return last_price <= lot.target_price


def _get_winner_side_from_sim(
    *,
    eligible_lots: list[SimLot],
    long_ready: bool,
    short_ready: bool,
    long_upnl: float,
    short_upnl: float,
) -> str:
    if eligible_lots:
        long_qty = sum(l.qty_remaining for l in eligible_lots if l.side == "LONG")
        short_qty = sum(l.qty_remaining for l in eligible_lots if l.side == "SHORT")
        if long_qty >= short_qty:
            return "LONG"
        return "SHORT"

    if long_ready and short_ready:
        return "LONG" if long_upnl >= short_upnl else "SHORT"
    if long_ready:
        return "LONG"
    return "SHORT"


def _resolve_side_bias(
    *,
    initial_side: str,
    long_contracts: float,
    short_contracts: float,
    long_upnl: float,
    short_upnl: float,
    hedge_loss_usdt: float,
    smaller_side: str,
) -> tuple[str, bool]:
    side_to_open = initial_side
    hedge_loss_triggered = False

    if long_contracts > 0 and short_contracts <= 0:
        side_to_open = "SHORT"
        hedge_loss_triggered = long_upnl <= float(hedge_loss_usdt)
    elif short_contracts > 0 and long_contracts <= 0:
        side_to_open = "LONG"
        hedge_loss_triggered = short_upnl <= float(hedge_loss_usdt)
    elif long_contracts > 0 and short_contracts > 0:
        side_to_open = smaller_side if smaller_side else initial_side

    return side_to_open, hedge_loss_triggered


def _compute_side_balance(long_contracts: float, short_contracts: float) -> tuple[str, str]:
    if long_contracts > short_contracts:
        return "LONG", "SHORT"
    if short_contracts > long_contracts:
        return "SHORT", "LONG"
    return "", ""


def _build_scenario_prices(
    *,
    start_price: float,
    steps: int,
    scenario: str,
    drift_pct_per_step: float = 0.0,
    oscillation_pct: float = 0.0,
    shock_step: Optional[int] = None,
    shock_pct: float = 0.0,
) -> list[float]:
    scenario = str(scenario or "").strip().lower()
    steps = int(max(1, steps))
    start_price = float(start_price)

    prices: list[float] = []
    price = start_price

    for i in range(steps):
        if scenario == "flat":
            pass

        elif scenario == "trend_up":
            price *= 1.0 + abs(float(drift_pct_per_step)) / 100.0

        elif scenario == "trend_down":
            price *= 1.0 - abs(float(drift_pct_per_step)) / 100.0

        elif scenario == "range":
            phase = -1.0 if (i % 2) else 1.0
            price *= 1.0 + phase * abs(float(oscillation_pct)) / 100.0

        elif scenario == "grind_down_bounce":
            if i < max(1, steps // 2):
                price *= 1.0 - abs(float(drift_pct_per_step)) / 100.0
            else:
                price *= 1.0 + abs(float(oscillation_pct)) / 100.0

        elif scenario == "shock_down_recover":
            if shock_step is not None and i == int(shock_step):
                price *= 1.0 - abs(float(shock_pct)) / 100.0
            elif shock_step is not None and i > int(shock_step):
                price *= 1.0 + abs(float(oscillation_pct)) / 100.0

        elif scenario == "shock_up_revert":
            if shock_step is not None and i == int(shock_step):
                price *= 1.0 + abs(float(shock_pct)) / 100.0
            elif shock_step is not None and i > int(shock_step):
                price *= 1.0 - abs(float(oscillation_pct)) / 100.0

        else:
            raise ValueError(f"Unsupported scenario: {scenario}")

        prices.append(max(price, 1e-9))

    return prices


def simulate_strategy_market(
    *,
    symbol: str,
    initial_side: str,
    start_price: float,
    steps: int,
    scenario: str,
    starting_capital: float,
    secured_capital: float,
    leverage: float,
    hedge_loss_usdt: float,
    target_roi_pct: float,
    limit_offset_pct: float,
    open_limit_offset_pct: float,
    rebalance_trigger_pct: float,
    moderate_imbalance_ratio: float,
    extreme_imbalance_ratio: float,
    safe_mode_one_contract: bool,
    contract_step: float,
    drift_pct_per_step: float = 0.20,
    oscillation_pct: float = 0.35,
    shock_step: Optional[int] = None,
    shock_pct: float = 5.0,
    fatal_drawdown_pct: float = 50.0,
    fatal_margin_ratio_pct: float = 80.0,
    fatal_gross_multiplier: float = 8.0,
) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    initial_side = _normalize_side(initial_side)

    price_series = _build_scenario_prices(
        start_price=float(start_price),
        steps=int(steps),
        scenario=scenario,
        drift_pct_per_step=float(drift_pct_per_step),
        oscillation_pct=float(oscillation_pct),
        shock_step=shock_step,
        shock_pct=float(shock_pct),
    )

    regime_params = get_strategy_regime_params(float(starting_capital), float(secured_capital))

    long_qty = 0.0
    short_qty = 0.0
    long_entry = 0.0
    short_entry = 0.0
    lots: list[SimLot] = []

    peak_equity = float(starting_capital)
    min_equity = float(starting_capital)

    action_rows: list[dict[str, Any]] = []
    fatal_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []

    for step_idx, current_last_price in enumerate(price_series, start=1):
        long_upnl = _compute_leg_upnl("LONG", long_qty, long_entry, current_last_price)
        short_upnl = _compute_leg_upnl("SHORT", short_qty, short_entry, current_last_price)
        total_upnl = long_upnl + short_upnl

        equity = float(starting_capital) + float(total_upnl)
        peak_equity = max(peak_equity, equity)
        min_equity = min(min_equity, equity)

        active_strategy_capital = max(0.0, equity - float(secured_capital))

        sizing = compute_strategy_sizing(
            active_strategy_capital=active_strategy_capital,
            last_price=current_last_price,
            leverage=float(leverage),
            long_contracts=long_qty,
            short_contracts=short_qty,
            initial_entry_pct=float(regime_params["initial_entry_pct"]),
            rebalance_step_pct=float(regime_params["rebalance_step_pct"]),
            max_symbol_envelope_pct=float(regime_params["max_symbol_envelope_pct"]),
            max_action_contracts_cap=float(regime_params["max_action_contracts_cap"]),
            min_action_contracts=1.0,
            contract_step=float(contract_step),
        )

        if safe_mode_one_contract:
            if float(sizing.get("final_initial_contracts") or 0.0) >= 1.0:
                sizing["final_initial_contracts"] = 1.0
            if float(sizing.get("final_rebalance_contracts") or 0.0) >= 1.0:
                sizing["final_rebalance_contracts"] = 1.0

        target_move_pct = float(target_roi_pct) / float(leverage) / 100.0

        long_target_price = long_entry * (1.0 + target_move_pct) if long_entry > 0 else None
        short_target_price = short_entry * (1.0 - target_move_pct) if short_entry > 0 else None

        long_ready = (
            current_last_price is not None
            and long_target_price is not None
            and current_last_price >= long_target_price
        )
        short_ready = (
            current_last_price is not None
            and short_target_price is not None
            and current_last_price <= short_target_price
        )

        eligible_lots = [lot for lot in lots if _compute_lot_target_ready(lot, current_last_price)]
        lot_trim_ready = len(eligible_lots) > 0
        leg_trim_ready = long_ready or short_ready

        imbalance_ratio = compute_imbalance_ratio(long_qty, short_qty)
        bigger_side, smaller_side = _compute_side_balance(long_qty, short_qty)

        valid_rebalance_signal = (
            min(long_qty, short_qty) > 0
            and has_valid_rebalance_signal(
                smaller_side=smaller_side,
                last_price=current_last_price,
                long_entry=long_entry,
                short_entry=short_entry,
                rebalance_trigger_pct=float(rebalance_trigger_pct),
            )
        )

        side_to_open, hedge_loss_triggered = _resolve_side_bias(
            initial_side=initial_side,
            long_contracts=long_qty,
            short_contracts=short_qty,
            long_upnl=long_upnl,
            short_upnl=short_upnl,
            hedge_loss_usdt=hedge_loss_usdt,
            smaller_side=smaller_side,
        )

        open_class = classify_open_action(
            long_contracts=long_qty,
            short_contracts=short_qty,
            side_to_open=side_to_open,
            regime=regime_params["regime"],
            hedge_loss_triggered=hedge_loss_triggered,
            valid_rebalance_signal=valid_rebalance_signal,
            imbalance_ratio=imbalance_ratio,
            moderate_imbalance_ratio=float(moderate_imbalance_ratio),
            extreme_imbalance_ratio=float(extreme_imbalance_ratio),
        )

        close_class = classify_close_action(
            lot_trim_ready=lot_trim_ready,
            leg_trim_ready=leg_trim_ready,
            imbalance_ratio=imbalance_ratio,
            moderate_imbalance_ratio=float(moderate_imbalance_ratio),
            extreme_imbalance_ratio=float(extreme_imbalance_ratio),
        )

        decision = None
        strategy_state = "UNDEFINED"
        action_reason = ""
        no_action_reason = ""

        if long_qty <= 0 and short_qty <= 0:
            strategy_state = "NO_POSITION"

            if open_class == "INITIAL" and float(sizing["final_initial_contracts"]) >= 1.0:
                open_limit_price = build_open_limit_price(
                    side=initial_side,
                    current_last_price=current_last_price,
                    open_limit_offset_pct=float(open_limit_offset_pct),
                )
                if open_limit_price is not None:
                    decision = (
                        "OPEN_LIMIT",
                        initial_side,
                        float(sizing["final_initial_contracts"]),
                        float(open_limit_price),
                        f"auto: initial {initial_side} | regime={regime_params['regime']} | class={open_class}",
                    )
                    action_reason = f"INITIAL_ENTRY_{initial_side}"
                else:
                    no_action_reason = "INITIAL_ENTRY_NO_MARKET_PRICE"
            else:
                no_action_reason = "INITIAL_ENTRY_SIZE_BELOW_MIN"

        elif long_qty > 0 and short_qty <= 0:
            strategy_state = "ONE_SIDED_LONG"

            if open_class == "HEDGE" and float(sizing["final_rebalance_contracts"]) >= 1.0:
                open_limit_price = build_open_limit_price(
                    side="SHORT",
                    current_last_price=current_last_price,
                    open_limit_offset_pct=float(open_limit_offset_pct),
                )
                if open_limit_price is not None:
                    decision = (
                        "OPEN_LIMIT",
                        "SHORT",
                        float(sizing["final_rebalance_contracts"]),
                        float(open_limit_price),
                        f"auto: hedge SHORT | regime={regime_params['regime']} | class={open_class}",
                    )
                    action_reason = "ONE_SIDED_HEDGE_TRIGGER_SHORT"
                else:
                    no_action_reason = "ONE_SIDED_LONG_NO_MARKET_PRICE"
            else:
                no_action_reason = "ONE_SIDED_LONG_NO_HEDGE_TRIGGER"

        elif short_qty > 0 and long_qty <= 0:
            strategy_state = "ONE_SIDED_SHORT"

            if open_class == "HEDGE" and float(sizing["final_rebalance_contracts"]) >= 1.0:
                open_limit_price = build_open_limit_price(
                    side="LONG",
                    current_last_price=current_last_price,
                    open_limit_offset_pct=float(open_limit_offset_pct),
                )
                if open_limit_price is not None:
                    decision = (
                        "OPEN_LIMIT",
                        "LONG",
                        float(sizing["final_rebalance_contracts"]),
                        float(open_limit_price),
                        f"auto: hedge LONG | regime={regime_params['regime']} | class={open_class}",
                    )
                    action_reason = "ONE_SIDED_HEDGE_TRIGGER_LONG"
                else:
                    no_action_reason = "ONE_SIDED_SHORT_NO_MARKET_PRICE"
            else:
                no_action_reason = "ONE_SIDED_SHORT_NO_HEDGE_TRIGGER"

        elif long_qty > 0 and short_qty > 0:
            roi_trim_ready = leg_trim_ready or lot_trim_ready

            if close_class in ("LOT_TRIM", "LEG_TRIM", "HARVEST", "DE_RISK") and roi_trim_ready:
                winner_side = _get_winner_side_from_sim(
                    eligible_lots=eligible_lots,
                    long_ready=long_ready,
                    short_ready=short_ready,
                    long_upnl=long_upnl,
                    short_upnl=short_upnl,
                )

                winner_qty = long_qty if winner_side == "LONG" else short_qty
                trim_qty = 1.0 if safe_mode_one_contract else max(
                    1.0,
                    min(
                        winner_qty,
                        float(winner_qty) * (float(regime_params["trim_winner_pct"]) / 100.0),
                    ),
                )
                trim_qty = float(min(trim_qty, winner_qty))

                if trim_qty >= 1.0:
                    if winner_side == "LONG":
                        long_qty = max(0.0, long_qty - trim_qty)
                        lots_to_reduce = [lot for lot in lots if lot.side == "LONG"]
                    else:
                        short_qty = max(0.0, short_qty - trim_qty)
                        lots_to_reduce = [lot for lot in lots if lot.side == "SHORT"]

                    remaining_to_reduce = trim_qty
                    for lot in lots_to_reduce:
                        if remaining_to_reduce <= 0:
                            break
                        cut = min(lot.qty_remaining, remaining_to_reduce)
                        lot.qty_remaining -= cut
                        remaining_to_reduce -= cut

                    lots = [lot for lot in lots if lot.qty_remaining > 0]

                    if winner_side == "LONG" and long_qty <= 0:
                        long_entry = 0.0
                    if winner_side == "SHORT" and short_qty <= 0:
                        short_entry = 0.0

                    decision = ("CLOSE_LIMIT", winner_side, trim_qty, current_last_price, close_class)
                    strategy_state = f"HEDGED_{close_class}"
                    action_reason = f"{close_class}_TRIM_{winner_side}"
                else:
                    strategy_state = f"HEDGED_{close_class}"
                    no_action_reason = f"{close_class}_TRIM_SIZE_BELOW_MIN"

            elif open_class in ("REBALANCE", "RECOVERY_ADD") and float(sizing["final_rebalance_contracts"]) >= 1.0:
                side_for_open = smaller_side if smaller_side else side_to_open
                open_qty = float(sizing["final_rebalance_contracts"])
                open_limit_price = build_open_limit_price(
                    side=side_for_open,
                    current_last_price=current_last_price,
                    open_limit_offset_pct=float(open_limit_offset_pct),
                )

                if open_limit_price is not None and open_qty >= 1.0:
                    fill_price = float(open_limit_price)

                    if side_for_open == "LONG":
                        long_entry = _weighted_entry(long_qty, long_entry, open_qty, fill_price)
                        long_qty += open_qty
                    else:
                        short_entry = _weighted_entry(short_qty, short_entry, open_qty, fill_price)
                        short_qty += open_qty

                    lots.append(
                        SimLot(
                            side=side_for_open,
                            qty_remaining=open_qty,
                            entry_price=fill_price,
                            leverage=float(leverage),
                            target_roi_pct=float(target_roi_pct),
                            target_price=_target_price_for_lot(
                                side=side_for_open,
                                entry_price=fill_price,
                                leverage=float(leverage),
                                target_roi_pct=float(target_roi_pct),
                            ),
                        )
                    )

                    decision = ("OPEN_LIMIT", side_for_open, open_qty, fill_price, open_class)
                    strategy_state = f"HEDGED_{open_class}"
                    action_reason = f"{open_class}_{side_for_open}"
                else:
                    strategy_state = f"HEDGED_{open_class}"
                    no_action_reason = f"{open_class}_NO_MARKET_PRICE"
            else:
                strategy_state = "HEDGED_WAIT"
                no_action_reason = "NO_OPEN_OR_CLOSE_SIGNAL"

        gross_contracts = float(long_qty + short_qty)
        net_contracts = float(abs(long_qty - short_qty))
        margin_used_est = (gross_contracts * current_last_price / float(leverage)) if leverage > 0 else 0.0
        margin_ratio_est_pct = (margin_used_est / equity * 100.0) if equity > 0 else 999999.0
        drawdown_pct = ((peak_equity - equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0

        fatal_reasons: list[str] = []

        if equity <= 0:
            fatal_reasons.append("EQUITY_LE_ZERO")
        if drawdown_pct >= float(fatal_drawdown_pct):
            fatal_reasons.append(f"DRAWDOWN_GE_{float(fatal_drawdown_pct):.1f}PCT")
        if margin_ratio_est_pct >= float(fatal_margin_ratio_pct):
            fatal_reasons.append(f"MARGIN_RATIO_GE_{float(fatal_margin_ratio_pct):.1f}PCT")
        if gross_contracts >= max(1.0, float(fatal_gross_multiplier) * max(1.0, float(starting_capital))):
            fatal_reasons.append("GROSS_CONTRACTS_TOO_LARGE")
        if strategy_state.startswith("HEDGED_RECOVERY_ADD") and imbalance_ratio >= float(extreme_imbalance_ratio) * 2.0:
            fatal_reasons.append("RECOVERY_ADD_WITH_EXTREME_IMBALANCE")
        if min(long_qty, short_qty) <= 0 and gross_contracts > 0 and abs(total_upnl) > abs(float(hedge_loss_usdt)) * 3.0:
            fatal_reasons.append("ONE_SIDED_DEEP_LOSS")

        trace_row = {
            "step": step_idx,
            "price": current_last_price,
            "equity": equity,
            "peak_equity": peak_equity,
            "drawdown_pct": drawdown_pct,
            "long_qty": long_qty,
            "short_qty": short_qty,
            "gross_contracts": gross_contracts,
            "net_contracts": net_contracts,
            "long_entry": long_entry,
            "short_entry": short_entry,
            "long_upnl": long_upnl,
            "short_upnl": short_upnl,
            "total_upnl": total_upnl,
            "margin_used_est": margin_used_est,
            "margin_ratio_est_pct": margin_ratio_est_pct,
            "imbalance_ratio": imbalance_ratio,
            "strategy_state": strategy_state,
            "action_reason": action_reason,
            "no_action_reason": no_action_reason,
            "open_class": open_class,
            "close_class": close_class,
            "decision": str(decision),
            "lot_trim_ready": lot_trim_ready,
            "leg_trim_ready": leg_trim_ready,
            "fatal": bool(fatal_reasons),
            "fatal_reasons": " | ".join(fatal_reasons),
        }
        trace_rows.append(trace_row)

        if decision is not None:
            action_rows.append(
                {
                    "step": step_idx,
                    "price": current_last_price,
                    "strategy_state": strategy_state,
                    "action_reason": action_reason,
                    "decision": str(decision),
                    "long_qty": long_qty,
                    "short_qty": short_qty,
                    "equity": equity,
                }
            )

        if fatal_reasons:
            fatal_rows.append(trace_row.copy())

    trace_df = pd.DataFrame(trace_rows)
    actions_df = pd.DataFrame(action_rows)
    fatal_df = pd.DataFrame(fatal_rows)

    result = {
        "summary": {
            "symbol": symbol,
            "scenario": scenario,
            "steps": int(steps),
            "start_price": float(start_price),
            "final_price": float(price_series[-1]) if price_series else float(start_price),
            "starting_capital": float(starting_capital),
            "secured_capital": float(secured_capital),
            "leverage": float(leverage),
            "final_equity": float(trace_df.iloc[-1]["equity"]) if not trace_df.empty else float(starting_capital),
            "peak_equity": float(trace_df["peak_equity"].max()) if not trace_df.empty else float(starting_capital),
            "min_equity": float(trace_df["equity"].min()) if not trace_df.empty else float(starting_capital),
            "max_drawdown_pct": float(trace_df["drawdown_pct"].max()) if not trace_df.empty else 0.0,
            "max_margin_ratio_est_pct": float(trace_df["margin_ratio_est_pct"].max()) if not trace_df.empty else 0.0,
            "max_gross_contracts": float(trace_df["gross_contracts"].max()) if not trace_df.empty else 0.0,
            "final_long_qty": float(long_qty),
            "final_short_qty": float(short_qty),
            "final_long_entry": float(long_entry),
            "final_short_entry": float(short_entry),
            "actions_count": int(len(actions_df)),
            "fatal_count": int(len(fatal_df)),
            "first_fatal_step": None if fatal_df.empty else int(fatal_df.iloc[0]["step"]),
            "first_fatal_reason": None if fatal_df.empty else str(fatal_df.iloc[0]["fatal_reasons"]),
        },
        "trace_df": trace_df,
        "actions_df": actions_df,
        "fatal_df": fatal_df,
    }
    return result
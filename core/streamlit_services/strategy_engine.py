from __future__ import annotations

import time
from typing import Any, Callable, Optional

import pandas as pd

from core.close_classifier import classify_close_action
from core.mexc_direct import futures_symbol_raw, get_live_ticker_snapshot
from core.open_classifier import classify_open_action
from core.streamlit_services.common import _to_float_series
from core.streamlit_services.dashboard_service import (
    get_latest_account_snapshot,
    get_latest_positions_per_symbol_side,
)
from core.streamlit_services.lots_service import (
    compute_eligible_lots_df,
    get_open_lots_for_symbol,
)
from core.streamlit_services.strategy_service import (
    build_open_limit_price,
    build_trim_decision,
    compute_imbalance_ratio,
    compute_strategy_sizing,
    get_strategy_regime_params,
    get_winner_side_from_lots_or_leg,
    has_active_pending_chase_for_symbol,
    has_active_pending_limit_for_symbol,
    has_active_task_for_symbol,
    has_valid_rebalance_signal,
)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _normalize_side(side: str) -> str:
    raw = str(side or "").strip().upper()
    return raw if raw in ("LONG", "SHORT") else raw


def _build_symbol_aliases(symbol: str) -> list[str]:
    raw = _normalize_symbol(symbol)
    if not raw:
        return []

    aliases = {raw}

    if ":" in raw:
        aliases.add(raw.split(":", 1)[0])

    try:
        raw_symbol = futures_symbol_raw(raw)
        if raw_symbol:
            aliases.add(str(raw_symbol).strip().upper())
    except Exception:
        pass

    if "_" in raw:
        parts = raw.split("_", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            aliases.add(f"{parts[0]}/{parts[1]}")
            aliases.add(f"{parts[0]}/{parts[1]}:USDT")

    if "/" in raw and ":" not in raw:
        aliases.add(f"{raw}:USDT")

    return sorted(str(x).strip().upper() for x in aliases if str(x).strip())


def _build_positions_df(pos_rows) -> pd.DataFrame:
    df_pos = pd.DataFrame(pos_rows) if pos_rows else pd.DataFrame(
        columns=["symbol", "side", "contracts", "entry_price", "unrealized_pnl"]
    )

    if not df_pos.empty:
        df_pos["symbol"] = df_pos["symbol"].fillna("").astype(str).str.upper()
        df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
        _to_float_series(df_pos, "contracts")
        _to_float_series(df_pos, "entry_price")
        _to_float_series(df_pos, "unrealized_pnl")

    return df_pos


def _build_open_lots_df(open_lot_rows) -> pd.DataFrame:
    df_open_lots = pd.DataFrame(open_lot_rows) if open_lot_rows else pd.DataFrame()

    if not df_open_lots.empty:
        df_open_lots["symbol"] = df_open_lots["symbol"].fillna("").astype(str).str.upper()
        df_open_lots["side"] = df_open_lots["side"].fillna("").astype(str).str.upper()
        for col in [
            "qty_opened",
            "qty_remaining",
            "entry_price",
            "target_roi_pct",
            "leverage",
            "target_price",
        ]:
            _to_float_series(df_open_lots, col)

    return df_open_lots


def _resolve_current_last_price(symbol: str, aliases: list[str]) -> float | None:
    symbols_to_try: list[str] = []
    base_symbol = _normalize_symbol(symbol)

    if base_symbol:
        symbols_to_try.append(base_symbol)

    for alias in aliases:
        alias_norm = _normalize_symbol(alias)
        if alias_norm and alias_norm not in symbols_to_try:
            symbols_to_try.append(alias_norm)

    for candidate in symbols_to_try:
        try:
            snap = get_live_ticker_snapshot(candidate)
        except Exception:
            snap = None

        if not snap:
            continue

        for key in ("last_price", "mark_price", "index_price", "bid_price", "ask_price"):
            value = snap.get(key)
            try:
                f = float(value)
                if f > 0:
                    return f
            except Exception:
                continue

    return None


def _get_leg_for_aliases(df_pos: pd.DataFrame, aliases: list[str], side: str) -> dict:
    if df_pos.empty:
        return {
            "contracts": 0.0,
            "unrealized_pnl": 0.0,
            "entry_price": 0.0,
        }

    side_norm = _normalize_side(side)
    rows = df_pos[
        df_pos["symbol"].isin(aliases)
        & (df_pos["side"] == side_norm)
    ]
    if rows.empty:
        return {
            "contracts": 0.0,
            "unrealized_pnl": 0.0,
            "entry_price": 0.0,
        }

    total_contracts = float(rows["contracts"].sum())
    weighted_entry_numerator = float((rows["entry_price"] * rows["contracts"]).sum())
    weighted_entry = weighted_entry_numerator / total_contracts if total_contracts > 0 else 0.0

    return {
        "contracts": total_contracts,
        "unrealized_pnl": float(rows["unrealized_pnl"].sum()),
        "entry_price": weighted_entry,
    }


def _get_open_lots_for_aliases(con, aliases: list[str]) -> list[dict]:
    out: list[dict] = []
    seen_ids = set()

    for alias in aliases:
        try:
            rows = get_open_lots_for_symbol(con, alias)
        except Exception:
            rows = []

        for row in rows:
            row_id = row.get("id")
            dedupe_key = row_id if row_id is not None else (
                row.get("symbol"),
                row.get("side"),
                row.get("entry_price"),
                row.get("qty_remaining"),
                row.get("created_at"),
            )
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            out.append(dict(row))

    out.sort(key=lambda r: (r.get("id") is None, r.get("id", 0)))
    return out


def _has_guard_for_aliases(
    con,
    aliases: list[str],
    checker: Callable[[Any, str], bool],
) -> bool:
    seen = set()
    for alias in aliases:
        alias_norm = _normalize_symbol(alias)
        if not alias_norm or alias_norm in seen:
            continue
        seen.add(alias_norm)
        try:
            if checker(con, alias_norm):
                return True
        except Exception:
            continue
    return False


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


def evaluate_strategy_state(
    *,
    con,
    symbol: str,
    initial_side: str,
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
    cooldown_sec_override: Optional[float],
    last_action_ts: float,
    gross_cap_contracts: Optional[float] = None,
) -> dict[str, Any]:
    symbol = _normalize_symbol(symbol)
    initial_side = _normalize_side(initial_side)

    acc = get_latest_account_snapshot(con)
    pos_ts, pos_rows = get_latest_positions_per_symbol_side(con)

    aliases = _build_symbol_aliases(symbol)
    if symbol and symbol not in aliases:
        aliases = sorted({symbol, *aliases})

    df_pos = _build_positions_df(pos_rows)

    long_leg = _get_leg_for_aliases(df_pos, aliases, "LONG")
    short_leg = _get_leg_for_aliases(df_pos, aliases, "SHORT")
    current_last_price = _resolve_current_last_price(symbol, aliases)

    open_lot_rows = _get_open_lots_for_aliases(con, aliases)
    df_open_lots = _build_open_lots_df(open_lot_rows)

    eligible_lots_df = (
        compute_eligible_lots_df(df_open_lots, current_last_price)
        if not df_open_lots.empty and current_last_price is not None
        else pd.DataFrame()
    )

    eligible_open_lots_count = int(len(eligible_lots_df)) if not eligible_lots_df.empty else 0
    eligible_open_qty = float(eligible_lots_df["qty_remaining"].sum()) if not eligible_lots_df.empty else 0.0

    total_equity = float((acc or {}).get("equity") or 0.0)
    active_strategy_capital = max(0.0, total_equity - float(secured_capital))
    regime_params = get_strategy_regime_params(float(starting_capital), float(secured_capital))

    long_contracts = float(long_leg["contracts"] or 0.0)
    short_contracts = float(short_leg["contracts"] or 0.0)
    gross_contracts = float(max(0.0, long_contracts) + max(0.0, short_contracts))

    sizing = compute_strategy_sizing(
        active_strategy_capital=active_strategy_capital,
        last_price=current_last_price,
        leverage=float(leverage),
        long_contracts=long_contracts,
        short_contracts=short_contracts,
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

    long_entry = float(long_leg["entry_price"] or 0.0)
    short_entry = float(short_leg["entry_price"] or 0.0)
    long_upnl = float(long_leg["unrealized_pnl"] or 0.0)
    short_upnl = float(short_leg["unrealized_pnl"] or 0.0)

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

    imbalance_ratio = compute_imbalance_ratio(long_contracts, short_contracts)
    bigger_side, smaller_side = _compute_side_balance(long_contracts, short_contracts)

    valid_rebalance_signal = (
        min(long_contracts, short_contracts) > 0
        and has_valid_rebalance_signal(
            smaller_side=smaller_side,
            last_price=current_last_price,
            long_entry=long_entry,
            short_entry=short_entry,
            rebalance_trigger_pct=float(rebalance_trigger_pct),
        )
    )

    guards = {
        "active_queue_task": _has_guard_for_aliases(con, aliases, has_active_task_for_symbol),
        "active_pending_limit": _has_guard_for_aliases(con, aliases, has_active_pending_limit_for_symbol),
        "active_pending_chase": _has_guard_for_aliases(con, aliases, has_active_pending_chase_for_symbol),
    }

    lot_trim_ready = eligible_open_lots_count > 0
    leg_trim_ready = long_ready or short_ready

    now = time.time()
    cooldown_sec = (
        float(cooldown_sec_override)
        if cooldown_sec_override is not None
        else float(regime_params["cooldown_sec"])
    )
    cooldown_left = max(0.0, cooldown_sec - (now - float(last_action_ts)))

    decision = None
    strategy_state = "UNDEFINED"
    action_reason = ""
    no_action_reason = ""
    open_class = None
    close_class = None

    gross_cap_enabled = gross_cap_contracts is not None and float(gross_cap_contracts) > 0
    gross_cap_value = float(gross_cap_contracts) if gross_cap_enabled else 0.0
    gross_cap_blocked = gross_cap_enabled and gross_contracts >= gross_cap_value

    if cooldown_left > 0:
        strategy_state = "COOLDOWN"
        no_action_reason = f"COOLDOWN_ACTIVE ({cooldown_left:.1f}s remaining)"

    elif guards["active_queue_task"]:
        strategy_state = "BLOCKED"
        no_action_reason = "BLOCKED_BY_ACTIVE_QUEUE_TASK"

    elif guards["active_pending_limit"]:
        strategy_state = "BLOCKED"
        no_action_reason = "BLOCKED_BY_PENDING_LIMIT"

    elif guards["active_pending_chase"]:
        strategy_state = "BLOCKED"
        no_action_reason = "BLOCKED_BY_PENDING_CHASE"

    else:
        side_to_open, hedge_loss_triggered = _resolve_side_bias(
            initial_side=initial_side,
            long_contracts=long_contracts,
            short_contracts=short_contracts,
            long_upnl=long_upnl,
            short_upnl=short_upnl,
            hedge_loss_usdt=hedge_loss_usdt,
            smaller_side=smaller_side,
        )

        open_class = classify_open_action(
            long_contracts=long_contracts,
            short_contracts=short_contracts,
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

        if long_contracts <= 0 and short_contracts <= 0:
            strategy_state = "NO_POSITION"

            if open_class == "INITIAL" and float(sizing["final_initial_contracts"]) >= 1:
                if gross_cap_blocked:
                    strategy_state = "BLOCKED"
                    no_action_reason = f"BLOCKED_BY_GROSS_CAP ({gross_contracts:.4f} >= {gross_cap_value:.4f})"
                else:
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

        elif long_contracts > 0 and short_contracts <= 0:
            strategy_state = "ONE_SIDED_LONG"

            if open_class == "HEDGE" and float(sizing["final_rebalance_contracts"]) >= 1:
                if gross_cap_blocked:
                    strategy_state = "BLOCKED"
                    no_action_reason = f"BLOCKED_BY_GROSS_CAP ({gross_contracts:.4f} >= {gross_cap_value:.4f})"
                else:
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

        elif short_contracts > 0 and long_contracts <= 0:
            strategy_state = "ONE_SIDED_SHORT"

            if open_class == "HEDGE" and float(sizing["final_rebalance_contracts"]) >= 1:
                if gross_cap_blocked:
                    strategy_state = "BLOCKED"
                    no_action_reason = f"BLOCKED_BY_GROSS_CAP ({gross_contracts:.4f} >= {gross_cap_value:.4f})"
                else:
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

        elif long_contracts > 0 and short_contracts > 0:
            roi_trim_ready = leg_trim_ready or lot_trim_ready

            if close_class in ("LOT_TRIM", "LEG_TRIM", "HARVEST", "DE_RISK") and roi_trim_ready:
                winner_side = get_winner_side_from_lots_or_leg(
                    lot_trim_ready=lot_trim_ready,
                    eligible_lots_df=eligible_lots_df,
                    long_ready=long_ready,
                    short_ready=short_ready,
                    long_upnl=long_upnl,
                    short_upnl=short_upnl,
                )

                reason_suffix = (
                    f"{close_class.lower()} | lot-eligible"
                    if lot_trim_ready
                    else f"{close_class.lower()} | leg-target"
                )

                decision = build_trim_decision(
                    winner_side=winner_side,
                    long_contracts=long_contracts,
                    short_contracts=short_contracts,
                    long_target_price=long_target_price,
                    short_target_price=short_target_price,
                    trim_winner_pct=float(regime_params["trim_winner_pct"]),
                    limit_offset_pct=float(limit_offset_pct),
                    safe_mode_one_contract=bool(safe_mode_one_contract),
                    contract_step=float(contract_step),
                    reason_suffix=reason_suffix,
                )

                if decision:
                    strategy_state = f"HEDGED_{close_class}"
                    action_reason = f"{close_class}_TRIM_{winner_side}"
                else:
                    strategy_state = f"HEDGED_{close_class}"
                    no_action_reason = f"{close_class}_TRIM_SIZE_BELOW_MIN"

            elif open_class in ("REBALANCE", "RECOVERY_ADD") and float(sizing["final_rebalance_contracts"]) >= 1:
                if gross_cap_blocked:
                    strategy_state = "BLOCKED"
                    no_action_reason = f"BLOCKED_BY_GROSS_CAP ({gross_contracts:.4f} >= {gross_cap_value:.4f})"
                else:
                    side_for_open = smaller_side if smaller_side else side_to_open
                    open_limit_price = build_open_limit_price(
                        side=side_for_open,
                        current_last_price=current_last_price,
                        open_limit_offset_pct=float(open_limit_offset_pct),
                    )

                    if open_limit_price is not None:
                        decision = (
                            "OPEN_LIMIT",
                            side_for_open,
                            float(sizing["final_rebalance_contracts"]),
                            float(open_limit_price),
                            f"auto: {open_class.lower()} {side_for_open} | regime={regime_params['regime']} | ratio={imbalance_ratio:.3f}",
                        )
                        strategy_state = f"HEDGED_{open_class}"
                        action_reason = f"{open_class}_{side_for_open}"
                    else:
                        strategy_state = f"HEDGED_{open_class}"
                        no_action_reason = f"{open_class}_NO_MARKET_PRICE"
            else:
                strategy_state = "HEDGED_WAIT"
                no_action_reason = "NO_OPEN_OR_CLOSE_SIGNAL"

        else:
            strategy_state = "UNKNOWN"
            no_action_reason = "UNKNOWN_STATE"

    return {
        "snapshot": {
            "account_ts": (acc or {}).get("created_at"),
            "positions_ts": pos_ts,
            "total_equity": total_equity,
            "secured_capital": float(secured_capital),
            "active_strategy_capital": active_strategy_capital,
        },
        "symbol_debug": {
            "symbol": symbol,
            "aliases": aliases,
        },
        "L": long_leg,
        "S": short_leg,
        "current_last_price": current_last_price,
        "sizing": sizing,
        "derived_targets": {
            "target_roi_pct": float(target_roi_pct),
            "leverage": float(leverage),
            "target_move_pct": target_move_pct,
            "long_target_price": long_target_price,
            "short_target_price": short_target_price,
            "safe_mode_one_contract": bool(safe_mode_one_contract),
        },
        "imbalance_block": {
            "imbalance_ratio": imbalance_ratio,
            "bigger_side": bigger_side,
            "smaller_side": smaller_side,
            "valid_rebalance_signal": valid_rebalance_signal,
            "moderate_threshold": float(moderate_imbalance_ratio),
            "extreme_threshold": float(extreme_imbalance_ratio),
        },
        "gross_block": {
            "gross_contracts": gross_contracts,
            "gross_cap_enabled": bool(gross_cap_enabled),
            "gross_cap_contracts": float(gross_cap_value) if gross_cap_enabled else 0.0,
            "gross_cap_blocked": bool(gross_cap_blocked),
        },
        "lot_debug": {
            "open_lots_count": int(len(df_open_lots)) if not df_open_lots.empty else 0,
            "eligible_open_lots_count": eligible_open_lots_count,
            "eligible_open_qty": eligible_open_qty,
        },
        "guards": guards,
        "decision": decision,
        "strategy_state": strategy_state,
        "action_reason": action_reason,
        "no_action_reason": no_action_reason,
        "open_class": open_class,
        "close_class": close_class,
        "regime_params": regime_params,
        "active_strategy_capital": active_strategy_capital,
        "imbalance_ratio": imbalance_ratio,
        "eligible_lots_df": eligible_lots_df if not eligible_lots_df.empty else None,
        "lot_trim_ready": lot_trim_ready,
        "leg_trim_ready": leg_trim_ready,
        "cooldown_left": cooldown_left,
    }
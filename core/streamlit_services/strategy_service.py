from __future__ import annotations

import math

from core.streamlit_services.common import now_utc_iso
from core.streamlit_services.contract_rules_service import (
    normalize_order_inputs,
    validate_contract_constraints,
)

LEGACY_PENDING_TASK_GUARDS_ENABLED = False


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _normalize_side(side: str) -> str:
    value = str(side or "").strip().upper()
    if value not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid side: {side}")
    return value


def has_active_task_for_symbol(con, symbol: str) -> bool:
    symbol = _normalize_symbol(symbol)
    row = con.execute(
        """
        SELECT 1
        FROM action_queue
        WHERE UPPER(symbol) = ?
          AND status IN ('ARMED', 'RUNNING')
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def has_active_pending_limit_for_symbol(con, symbol: str) -> bool:
    if not LEGACY_PENDING_TASK_GUARDS_ENABLED:
        return False

    symbol = _normalize_symbol(symbol)
    row = con.execute(
        """
        SELECT 1
        FROM pending_limit_tasks
        WHERE UPPER(symbol) = ?
          AND status IN ('PENDING', 'TRIGGERED')
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def has_active_pending_chase_for_symbol(con, symbol: str) -> bool:
    if not LEGACY_PENDING_TASK_GUARDS_ENABLED:
        return False

    symbol = _normalize_symbol(symbol)
    row = con.execute(
        """
        SELECT 1
        FROM pending_chase_tasks
        WHERE UPPER(symbol) = ?
          AND status = 'PENDING'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def enqueue_armed_open_limit(
    con,
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    leverage: float,
    note: str,
    priority: int = 50,
) -> None:
    from core.streamlit_services.queue_service import queue_insert_v2

    normalized = normalize_order_inputs(
        symbol=symbol,
        side=side,
        qty=float(qty),
        limit_price=float(limit_price),
        require_limit_price=True,
    )

    symbol = _normalize_symbol(normalized["symbol"])
    side = _normalize_side(normalized["side"])
    qty = float(normalized["qty"] or 0.0)
    limit_price = normalized["limit_price"]

    if qty <= 0:
        raise ValueError(f"Normalized qty is 0 for {symbol}; below min_qty or invalid for qty_step")

    if limit_price is None or float(limit_price) <= 0:
        raise ValueError(f"Normalized limit_price is invalid for {symbol}")

    validate_contract_constraints(
        symbol=symbol,
        qty=float(qty),
        limit_price=float(limit_price),
        leverage=float(leverage),
        require_limit_price=True,
    )

    ts = now_utc_iso()

    payload = {
        "created_at": ts,
        "created_by": "streamlit_v2:auto",
        "exchange": "mexc",
        "market_type": "swap",
        "symbol": symbol,
        "intent": "open",
        "side": side,
        "reduce_only": 0,
        "qty": float(qty),
        "qty_unit": "contracts",
        "order_type": "limit",
        "limit_price": float(limit_price),
        "slippage_bps": None,
        "trigger_type": "immediate",
        "trigger_op": None,
        "trigger_price": None,
        "trigger_timeout_sec": None,
        "min_free_margin": None,
        "max_margin_ratio": None,
        "max_spread_bps": None,
        "status": "ARMED",
        "priority": int(priority),
        "attempts": 0,
        "last_error": None,
        "last_update_at": ts,
        "ui_hint": None,
        "note": note.strip() if note else None,
        "panel_mode": "OPEN",
        "order_kind": "LIMIT",
        "leverage": int(float(leverage)),
    }
    queue_insert_v2(con, payload)
    con.commit()


def enqueue_armed_close_limit(
    con,
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    leverage: float,
    note: str,
    priority: int = 40,
) -> None:
    from core.streamlit_services.queue_service import queue_insert_v2

    normalized = normalize_order_inputs(
        symbol=symbol,
        side=side,
        qty=float(qty),
        limit_price=float(limit_price),
        require_limit_price=True,
    )

    symbol = _normalize_symbol(normalized["symbol"])
    side = _normalize_side(normalized["side"])
    qty = float(normalized["qty"] or 0.0)
    limit_price = normalized["limit_price"]

    if qty <= 0:
        raise ValueError(f"Normalized qty is 0 for {symbol}; below min_qty or invalid for qty_step")

    if limit_price is None or float(limit_price) <= 0:
        raise ValueError(f"Normalized limit_price is invalid for {symbol}")

    validate_contract_constraints(
        symbol=symbol,
        qty=float(qty),
        limit_price=float(limit_price),
        leverage=float(leverage),
        require_limit_price=True,
    )

    ts = now_utc_iso()

    payload = {
        "created_at": ts,
        "created_by": "streamlit_v2:auto",
        "exchange": "mexc",
        "market_type": "swap",
        "symbol": symbol,
        "intent": "close",
        "side": side,
        "reduce_only": 1,
        "qty": float(qty),
        "qty_unit": "contracts",
        "order_type": "limit",
        "limit_price": float(limit_price),
        "slippage_bps": None,
        "trigger_type": "immediate",
        "trigger_op": None,
        "trigger_price": None,
        "trigger_timeout_sec": None,
        "min_free_margin": None,
        "max_margin_ratio": None,
        "max_spread_bps": None,
        "status": "ARMED",
        "priority": int(priority),
        "attempts": 0,
        "last_error": None,
        "last_update_at": ts,
        "ui_hint": None,
        "note": note.strip() if note else None,
        "panel_mode": "CLOSE",
        "order_kind": "LIMIT",
        "leverage": int(float(leverage)),
    }
    queue_insert_v2(con, payload)
    con.commit()


def get_leg(df_pos, symbol: str, side: str) -> dict:
    if df_pos.empty:
        return {
            "contracts": 0.0,
            "unrealized_pnl": 0.0,
            "entry_price": 0.0,
        }

    symbol = _normalize_symbol(symbol)
    side = _normalize_side(side)

    rows = df_pos[
        (df_pos["symbol"].astype(str).str.upper() == symbol)
        & (df_pos["side"].astype(str).str.upper() == side)
    ]
    if rows.empty:
        return {
            "contracts": 0.0,
            "unrealized_pnl": 0.0,
            "entry_price": 0.0,
        }

    row = rows.iloc[0].to_dict()
    return {
        "contracts": float(row.get("contracts") or 0.0),
        "unrealized_pnl": float(row.get("unrealized_pnl") or 0.0),
        "entry_price": float(row.get("entry_price") or 0.0),
    }


def get_strategy_regime_params(starting_capital: float, secured_capital: float) -> dict:
    if float(secured_capital) < float(starting_capital):
        return {
            "regime": "RECOVERY",
            "initial_entry_pct": 20.0,
            "rebalance_step_pct": 10.0,
            "max_symbol_envelope_pct": 50.0,
            "trim_winner_pct": 10.0,
            "profit_recycle_pct": 50.0,
            "max_action_contracts_cap": 100.0,
            "cooldown_sec": 10.0,
        }

    return {
        "regime": "AGGRESSIVE",
        "initial_entry_pct": 35.0,
        "rebalance_step_pct": 15.0,
        "max_symbol_envelope_pct": 70.0,
        "trim_winner_pct": 12.5,
        "profit_recycle_pct": 80.0,
        "max_action_contracts_cap": 250.0,
        "cooldown_sec": 8.0,
    }


def floor_to_contract_step(raw_contracts: float, step: float = 1.0) -> float:
    if raw_contracts <= 0 or step <= 0:
        return 0.0
    return math.floor(raw_contracts / step) * step


def estimate_used_symbol_margin(
    long_contracts: float,
    short_contracts: float,
    last_price,
    leverage: float,
) -> float:
    if last_price is None or last_price <= 0 or leverage <= 0:
        return 0.0

    gross_contracts = max(0.0, float(long_contracts)) + max(0.0, float(short_contracts))
    gross_notional = gross_contracts * float(last_price)
    return gross_notional / float(leverage)


def margin_budget_to_contracts(
    margin_budget: float,
    last_price,
    leverage: float,
    contract_step: float = 1.0,
) -> float:
    if margin_budget <= 0 or last_price is None or last_price <= 0 or leverage <= 0:
        return 0.0

    raw_contracts = (float(margin_budget) * float(leverage)) / float(last_price)
    return floor_to_contract_step(raw_contracts, contract_step)


def compute_strategy_sizing(
    *,
    active_strategy_capital: float,
    last_price,
    leverage: float,
    long_contracts: float,
    short_contracts: float,
    initial_entry_pct: float,
    rebalance_step_pct: float,
    max_symbol_envelope_pct: float,
    max_action_contracts_cap: float,
    min_action_contracts: float = 1.0,
    contract_step: float = 1.0,
) -> dict:
    active_strategy_capital = max(0.0, float(active_strategy_capital))
    symbol_envelope_margin = active_strategy_capital * (float(max_symbol_envelope_pct) / 100.0)
    used_symbol_margin = estimate_used_symbol_margin(long_contracts, short_contracts, last_price, leverage)
    available_symbol_margin = max(0.0, symbol_envelope_margin - used_symbol_margin)

    requested_initial_margin_budget = active_strategy_capital * (float(initial_entry_pct) / 100.0)
    requested_rebalance_margin_budget = active_strategy_capital * (float(rebalance_step_pct) / 100.0)

    effective_initial_margin_budget = min(requested_initial_margin_budget, available_symbol_margin)
    effective_rebalance_margin_budget = min(requested_rebalance_margin_budget, available_symbol_margin)

    raw_initial_contracts = margin_budget_to_contracts(
        effective_initial_margin_budget,
        last_price,
        leverage,
        contract_step,
    )
    raw_rebalance_contracts = margin_budget_to_contracts(
        effective_rebalance_margin_budget,
        last_price,
        leverage,
        contract_step,
    )

    final_initial_contracts = min(raw_initial_contracts, float(max_action_contracts_cap))
    final_rebalance_contracts = min(raw_rebalance_contracts, float(max_action_contracts_cap))

    if final_initial_contracts < float(min_action_contracts):
        final_initial_contracts = 0.0
    if final_rebalance_contracts < float(min_action_contracts):
        final_rebalance_contracts = 0.0

    return {
        "symbol_envelope_margin": symbol_envelope_margin,
        "used_symbol_margin": used_symbol_margin,
        "available_symbol_margin": available_symbol_margin,
        "requested_initial_margin_budget": requested_initial_margin_budget,
        "requested_rebalance_margin_budget": requested_rebalance_margin_budget,
        "effective_initial_margin_budget": effective_initial_margin_budget,
        "effective_rebalance_margin_budget": effective_rebalance_margin_budget,
        "raw_initial_contracts": raw_initial_contracts,
        "raw_rebalance_contracts": raw_rebalance_contracts,
        "final_initial_contracts": final_initial_contracts,
        "final_rebalance_contracts": final_rebalance_contracts,
    }


def compute_imbalance_ratio(long_contracts: float, short_contracts: float) -> float:
    long_value = max(0.0, float(long_contracts))
    short_value = max(0.0, float(short_contracts))

    smaller = min(long_value, short_value)
    bigger = max(long_value, short_value)

    if smaller <= 0:
        return float("inf") if bigger > 0 else 0.0

    return bigger / smaller


def build_trim_decision(
    *,
    winner_side: str,
    long_contracts: float,
    short_contracts: float,
    long_target_price,
    short_target_price,
    trim_winner_pct: float,
    limit_offset_pct: float,
    safe_mode_one_contract: bool,
    contract_step: float,
    reason_suffix: str,
):
    winner_side = _normalize_side(winner_side)
    winner_contracts = long_contracts if winner_side == "LONG" else short_contracts

    if winner_contracts <= 0:
        return None

    if safe_mode_one_contract:
        trim_contracts = 1.0
    else:
        trim_contracts = max(
            1.0,
            floor_to_contract_step(
                winner_contracts * (float(trim_winner_pct) / 100.0),
                float(contract_step),
            ),
        )

    trim_contracts = float(min(trim_contracts, winner_contracts))
    if trim_contracts < 1:
        return None

    if winner_side == "LONG":
        if long_target_price is None:
            return None
        price_ref = float(long_target_price)
        limit_price = price_ref * (1.0 + float(limit_offset_pct) / 100.0)
    else:
        if short_target_price is None:
            return None
        price_ref = float(short_target_price)
        limit_price = price_ref * (1.0 - float(limit_offset_pct) / 100.0)

    return (
        "CLOSE_LIMIT",
        winner_side,
        trim_contracts,
        limit_price,
        f"auto: trim {winner_side} winner | {reason_suffix}",
    )


def has_valid_rebalance_signal(
    *,
    smaller_side: str,
    last_price,
    long_entry: float,
    short_entry: float,
    rebalance_trigger_pct: float,
) -> bool:
    if last_price is None or last_price <= 0:
        return False

    trigger_frac = float(rebalance_trigger_pct) / 100.0
    side = str(smaller_side).upper()

    if side == "LONG" and long_entry > 0:
        return float(last_price) >= float(long_entry) * (1.0 + trigger_frac)

    if side == "SHORT" and short_entry > 0:
        return float(last_price) <= float(short_entry) * (1.0 - trigger_frac)

    return False


def build_open_limit_price(
    *,
    side: str,
    current_last_price,
    open_limit_offset_pct: float,
):
    if current_last_price is None or current_last_price <= 0:
        return None

    side_upper = _normalize_side(side)

    if side_upper == "LONG":
        return float(current_last_price) * (1.0 - float(open_limit_offset_pct) / 100.0)

    if side_upper == "SHORT":
        return float(current_last_price) * (1.0 + float(open_limit_offset_pct) / 100.0)

    return None


def get_winner_side_from_lots_or_leg(
    *,
    lot_trim_ready: bool,
    eligible_lots_df,
    long_ready: bool,
    short_ready: bool,
    long_upnl: float,
    short_upnl: float,
) -> str:
    if lot_trim_ready and not eligible_lots_df.empty:
        side_qty = (
            eligible_lots_df.groupby("side", as_index=False)["qty_remaining"]
            .sum()
            .sort_values(["qty_remaining", "side"], ascending=[False, True])
        )
        return str(side_qty.iloc[0]["side"]).upper()

    if long_ready and short_ready:
        return "LONG" if long_upnl >= short_upnl else "SHORT"
    if long_ready:
        return "LONG"
    return "SHORT"


def summarize_strategy_state(result: dict) -> dict:
    return {
        "strategy_state": result.get("strategy_state"),
        "action_reason": result.get("action_reason"),
        "no_action_reason": result.get("no_action_reason"),
        "decision": result.get("decision"),
        "regime": (result.get("regime_params") or {}).get("regime"),
        "last_price": result.get("current_last_price"),
        "imbalance_ratio": result.get("imbalance_ratio"),
        "guards": result.get("guards"),
    }

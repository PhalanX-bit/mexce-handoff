from __future__ import annotations

import pandas as pd

TABLE_COLUMN_SETS: dict[str, list[str]] = {
    "market_positions": [
        "symbol",
        "side",
        "contracts",
        "entry_price",
        "unrealized_pnl",
        "created_at",
    ],
    "market_live_open_orders": [
        "orderId",
        "symbol",
        "price",
        "vol",
        "dealVol",
        "side",
        "state",
        "category",
        "createTime",
    ],
    "market_open_lots": [
        "id",
        "symbol",
        "side",
        "qty_remaining",
        "entry_price",
        "target_price",
        "opened_at",
        "status",
    ],
    "market_recent_queue": [
        "id",
        "symbol",
        "panel_mode",
        "order_kind",
        "side",
        "qty",
        "limit_price",
        "status",
        "priority",
        "last_update_at",
        "note",
    ],
    "market_health_rows": [
        "id",
        "symbol",
        "panel_mode",
        "order_kind",
        "side",
        "status",
        "reconcile_state",
        "decision_reason",
        "last_error",
        "last_update_at",
    ],
    "action_queue_main": [
        "id",
        "created_at",
        "symbol",
        "panel_mode",
        "order_kind",
        "side",
        "qty",
        "limit_price",
        "leverage",
        "trigger_type",
        "trigger_price",
        "status",
        "priority",
        "attempts",
        "last_error",
        "last_update_at",
        "note",
    ],
    "dashboard_positions": [
        "symbol",
        "side",
        "status",
        "contracts",
        "entry_price",
        "last_price",
        "mark_price",
        "unrealized_pnl",
        "created_at",
        "ticker_at",
    ],
    "dashboard_aggregates": [
        "symbol",
        "risk_flag",
        "gross_contracts",
        "net_contracts",
        "total_unrealized",
        "suggested_next_action",
    ],
    "controls_preview": [
        "action_id",
        "symbol",
        "api_order_id",
        "order_open",
        "current_order_price",
        "target_price",
        "drift_bps",
        "effective_threshold_bps",
        "max_reprice_distance_bps",
        "too_far_by_distance",
        "should_replace",
        "decision_reason",
        "stage",
        "applied",
    ],
    "strategy_log": [
        "ts",
        "symbol",
        "regime",
        "strategy_state",
        "action_reason",
        "no_action_reason",
        "decision",
        "dry",
        "LONG",
        "SHORT",
        "last_price",
        "imbalance_ratio",
        "lot_trim_ready",
        "leg_trim_ready",
        "open_class",
        "close_class",
    ],
}


def get_table_columns(table_key: str) -> list[str]:
    return list(TABLE_COLUMN_SETS.get(str(table_key or "").strip(), []))


def project_df_columns(df: pd.DataFrame | None, table_key: str) -> pd.DataFrame | None:
    if df is None or df.empty:
        return df

    preferred = get_table_columns(table_key)
    if not preferred:
        return df

    keep = [col for col in preferred if col in df.columns]
    if not keep:
        return df

    return df[keep].copy()
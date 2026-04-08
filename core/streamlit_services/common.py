from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float_series(df: pd.DataFrame, col: str) -> None:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)


def risk_flag(row, equity):
    gross = abs(float(row.get("gross_contracts") or 0.0))
    net = abs(float(row.get("net_contracts") or 0.0))

    if gross == 0:
        return "—"

    if net == gross:
        return "🔴 HIGH RISK – REDUCE ONLY"

    hedge_ratio = 1 - (net / gross)

    if hedge_ratio >= 0.75:
        return "🟢 HARVEST"
    if hedge_ratio >= 0.40:
        return "🟡 CONTROL"
    return "🟠 CAUTION"


def suggest_next_action(row):
    gross = float(row.get("gross_contracts") or 0.0)
    net = float(row.get("net_contracts") or 0.0)
    flag = str(row.get("risk_flag") or "")

    if gross == 0:
        return "No position."

    if abs(net) == abs(gross):
        side = "SHORT" if net < 0 else "LONG"
        return (
            f"One-sided {side}. Reduce only: close 10–15% on the next favorable move. "
            f"No adds; emergency hedge only if margin becomes tight."
        )

    hedge_ratio = 1 - (abs(net) / gross) if gross else 0.0

    if "HARVEST" in flag or hedge_ratio >= 0.75:
        return (
            "Hedged. Harvest oscillation: trim 5–10% from the profitable leg on a spike, "
            "then wait. Goal: reduce gross gradually while keeping net small."
        )

    if "CONTROL" in flag or hedge_ratio >= 0.40:
        return (
            "Partially hedged. Prefer trims over adds: trim the profitable side 5–10% at relief moves. "
            "Avoid increasing gross."
        )

    return (
        "Caution. Net exposure is relatively large: prioritize gross reduction. "
        "Trim profitable side first; avoid adding into moves."
    )
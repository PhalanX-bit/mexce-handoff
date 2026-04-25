# core/open_classifier.py
# Classify OPEN actions before enqueue

from typing import Optional


def classify_open_action(
    *,
    long_contracts: float,
    short_contracts: float,
    side_to_open: str,
    regime: str,
    hedge_loss_triggered: bool = False,
    valid_rebalance_signal: bool = False,
    imbalance_ratio: float = 0.0,
    moderate_imbalance_ratio: float = 1.5,
    extreme_imbalance_ratio: float = 3.0,
) -> str:
    """
    Returns one of:
    - INITIAL
    - HEDGE
    - REBALANCE
    - RECOVERY_ADD

    Rules:
    1. No existing position on either side -> INITIAL
    2. One-sided position + hedge loss trigger -> HEDGE
    3. Two-sided position + rebalance signal + imbalance -> REBALANCE
    4. Fallback add while in RECOVERY regime -> RECOVERY_ADD
    """

    side_to_open = str(side_to_open or "").upper()
    regime = str(regime or "").upper()

    l = max(0.0, float(long_contracts))
    s = max(0.0, float(short_contracts))

    no_position = l <= 0 and s <= 0
    one_sided = (l > 0 and s <= 0) or (s > 0 and l <= 0)
    two_sided = l > 0 and s > 0

    if no_position:
        return "INITIAL"

    if one_sided and hedge_loss_triggered:
        return "HEDGE"

    if two_sided and valid_rebalance_signal and imbalance_ratio >= moderate_imbalance_ratio:
        return "REBALANCE"

    if regime == "RECOVERY":
        return "RECOVERY_ADD"

    return "REBALANCE"
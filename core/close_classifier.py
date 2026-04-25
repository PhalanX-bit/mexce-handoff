# core/close_classifier.py
# Classify CLOSE actions before enqueue

def classify_close_action(
    *,
    lot_trim_ready: bool,
    leg_trim_ready: bool,
    imbalance_ratio: float,
    moderate_imbalance_ratio: float = 1.5,
    extreme_imbalance_ratio: float = 3.0,
) -> str:
    """
    Returns one of:
    - LOT_TRIM
    - LEG_TRIM
    - HARVEST
    - DE_RISK
    """

    if lot_trim_ready:
        return "LOT_TRIM"

    if leg_trim_ready and imbalance_ratio >= extreme_imbalance_ratio:
        return "DE_RISK"

    if leg_trim_ready and imbalance_ratio >= moderate_imbalance_ratio:
        return "HARVEST"

    if leg_trim_ready:
        return "LEG_TRIM"

    return "LEG_TRIM"
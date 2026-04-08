from __future__ import annotations

from typing import Optional

from core.mexc_direct import futures_symbol_raw


def canonical_futures_symbol(symbol: str | None) -> Optional[str]:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return None

    if "_" in raw:
        parts = raw.split("_", 1)
        if len(parts) == 2:
            raw = f"{parts[0]}/{parts[1]}"

    if raw.endswith("USDT") and "/" not in raw and ":" not in raw and len(raw) > 4:
        base = raw[:-4]
        raw = f"{base}/USDT:USDT"
        return raw

    if "/" in raw and ":" not in raw:
        return f"{raw}:USDT"

    if ":" in raw:
        left, right = raw.split(":", 1)
        if right == "USDT":
            return f"{left}:USDT"

    try:
        normalized_raw = futures_symbol_raw(raw)
        if normalized_raw:
            normalized_raw = str(normalized_raw).upper()
            if normalized_raw.endswith("USDT") and "/" not in normalized_raw and len(normalized_raw) > 4:
                base = normalized_raw[:-4]
                return f"{base}/USDT:USDT"
    except Exception:
        pass

    return raw


def symbols_match(left: str | None, right: str | None) -> bool:
    return canonical_futures_symbol(left) == canonical_futures_symbol(right)
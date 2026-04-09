from __future__ import annotations

from typing import Optional

from core.mexc_direct import futures_symbol_display, futures_symbol_raw, futures_symbol_slash


def canonical_futures_symbol(symbol: str | None) -> Optional[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return None

    value = value.replace(":USDT:USDT", ":USDT")
    value = value.replace("/USDT/USDT", "/USDT")

    if value.endswith("/USDT") and ":USDT" not in value:
        return f"{value}:USDT"

    try:
        return futures_symbol_display(value)
    except Exception:
        pass

    if value.endswith("USDT") and "/" not in value and ":" not in value and "_" not in value and len(value) > 4:
        base = value[:-4]
        if base:
            return f"{base}/USDT:USDT"

    return value


def build_symbol_aliases(symbol: str | None) -> list[str]:
    canonical = canonical_futures_symbol(symbol)
    if not canonical:
        return []

    aliases = {canonical}

    try:
        aliases.add(futures_symbol_slash(canonical).upper())
    except Exception:
        pass

    try:
        aliases.add(futures_symbol_raw(canonical).upper())
    except Exception:
        pass

    if ":" in canonical:
        aliases.add(canonical.split(":", 1)[0])

    return sorted(str(x).strip().upper() for x in aliases if str(x).strip())


def symbols_match(left: str | None, right: str | None) -> bool:
    return canonical_futures_symbol(left) == canonical_futures_symbol(right)

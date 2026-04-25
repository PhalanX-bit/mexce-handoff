from __future__ import annotations

from core.mexc_direct import futures_symbol_display, futures_symbol_raw

# Ако списъкът е празен, UI ще открива наличните futures symbols от DB.
# Ако го попълниш, точно този списък ще се използва навсякъде в UI.
GLOBAL_FUTURES_SYMBOLS: list[str] = [
    # "ADA/USDT:USDT",
    # "BTC/USDT:USDT",
    # "ETH/USDT:USDT",
    # "SOL/USDT:USDT",
]

DEFAULT_FUTURES_SYMBOL = "BTC/USDT:USDT"


def normalize_futures_symbol(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    try:
        return futures_symbol_display(futures_symbol_raw(raw))
    except Exception:
        return ""


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []

    for value in values:
        canonical = normalize_futures_symbol(value)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)

    return out


def load_futures_symbol_options(con) -> list[str]:
    if GLOBAL_FUTURES_SYMBOLS:
        return _dedupe_keep_order(GLOBAL_FUTURES_SYMBOLS)

    collected: list[str] = []

    def _append_rows(rows) -> None:
        for row in rows:
            raw = str(row[0] or "").strip()
            if not raw:
                continue
            collected.append(raw)

    try:
        rows = con.execute(
            """
            SELECT DISTINCT exchange_symbol
            FROM symbols_state
            WHERE exchange_symbol IS NOT NULL
              AND TRIM(exchange_symbol) <> ''
            ORDER BY exchange_symbol
            """
        ).fetchall()
        _append_rows(rows)
    except Exception:
        pass

    try:
        rows = con.execute(
            """
            SELECT DISTINCT symbol
            FROM symbols_state
            WHERE symbol IS NOT NULL
              AND TRIM(symbol) <> ''
            ORDER BY symbol
            """
        ).fetchall()
        _append_rows(rows)
    except Exception:
        pass

    try:
        rows = con.execute(
            """
            SELECT DISTINCT symbol
            FROM positions_snapshot
            WHERE symbol IS NOT NULL
              AND TRIM(symbol) <> ''
            ORDER BY symbol
            """
        ).fetchall()
        _append_rows(rows)
    except Exception:
        pass

    try:
        rows = con.execute(
            """
            SELECT DISTINCT symbol
            FROM action_queue
            WHERE symbol IS NOT NULL
              AND TRIM(symbol) <> ''
            ORDER BY symbol
            """
        ).fetchall()
        _append_rows(rows)
    except Exception:
        pass

    out = _dedupe_keep_order(collected)
    out.sort()
    return out


def resolve_default_symbol_index(options: list[str], default_symbol: str | None = None) -> int:
    if not options:
        return 0

    preferred = normalize_futures_symbol(default_symbol or DEFAULT_FUTURES_SYMBOL)
    if preferred and preferred in options:
        return options.index(preferred)

    return 0
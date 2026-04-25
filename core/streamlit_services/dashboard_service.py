from __future__ import annotations

import pandas as pd

from core.symbol_utils import build_symbol_aliases


def get_latest_account_snapshot(con):
    row = con.execute(
        """
        SELECT *
        FROM account_snapshot
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def get_latest_positions_per_symbol_side(con):
    rows = con.execute(
        """
        SELECT p.symbol, p.side, p.contracts, p.entry_price, p.unrealized_pnl, p.created_at
        FROM positions_snapshot p
        JOIN (
            SELECT symbol, side, MAX(id) AS max_id
            FROM positions_snapshot
            GROUP BY symbol, side
        ) latest
        ON p.id = latest.max_id
        ORDER BY p.symbol ASC, p.side ASC
        """
    ).fetchall()

    out = [dict(r) for r in rows]
    latest_ts = max((r.get("created_at") for r in out if r.get("created_at")), default=None)
    return latest_ts, out


def get_latest_positions_for_symbol(con, symbol: str):
    aliases = build_symbol_aliases(symbol)
    if aliases:
        placeholders = ",".join("?" for _ in aliases)
        rows = con.execute(
            f"""
            SELECT p.symbol, p.side, p.contracts, p.entry_price, p.unrealized_pnl, p.created_at
            FROM positions_snapshot p
            JOIN (
                SELECT symbol, side, MAX(id) AS max_id
                FROM positions_snapshot
                WHERE UPPER(symbol) IN ({placeholders})
                GROUP BY symbol, side
            ) latest
            ON p.id = latest.max_id
            ORDER BY p.symbol ASC, p.side ASC
            """,
            tuple(aliases),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT p.symbol, p.side, p.contracts, p.entry_price, p.unrealized_pnl, p.created_at
            FROM positions_snapshot p
            JOIN (
                SELECT symbol, side, MAX(id) AS max_id
                FROM positions_snapshot
                WHERE symbol = ?
                GROUP BY symbol, side
            ) latest
            ON p.id = latest.max_id
            ORDER BY p.symbol ASC, p.side ASC
            """,
            (symbol,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_tickers(con):
    rows = con.execute(
        """
        SELECT t.symbol, t.last_price, t.mark_price, t.created_at
        FROM ticker_snapshot t
        JOIN (
            SELECT symbol, MAX(id) AS max_id
            FROM ticker_snapshot
            GROUP BY symbol
        ) latest
        ON t.id = latest.max_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_ticker_for_symbol(con, symbol: str):
    aliases = build_symbol_aliases(symbol)
    if aliases:
        placeholders = ",".join("?" for _ in aliases)
        row = con.execute(
            f"""
            SELECT symbol, last_price, mark_price, created_at
            FROM ticker_snapshot
            WHERE UPPER(symbol) IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(aliases),
        ).fetchone()
    else:
        row = con.execute(
            """
            SELECT symbol, last_price, mark_price, created_at
            FROM ticker_snapshot
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    return dict(row) if row else None


def get_latest_ticker_price_for_symbol(con, symbol: str):
    aliases = build_symbol_aliases(symbol)
    if aliases:
        placeholders = ",".join("?" for _ in aliases)
        row = con.execute(
            f"""
            SELECT last_price
            FROM ticker_snapshot
            WHERE UPPER(symbol) IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(aliases),
        ).fetchone()
    else:
        row = con.execute(
            """
            SELECT last_price
            FROM ticker_snapshot
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()

    if not row:
        return None

    try:
        value = row["last_price"]
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def get_open_lot_summary(con):
    rows = con.execute(
        """
        SELECT
            symbol,
            side,
            COUNT(*) AS open_lot_rows,
            SUM(COALESCE(qty_remaining, 0)) AS qty_remaining_total,
            MIN(entry_price) AS min_entry_price,
            MAX(entry_price) AS max_entry_price,
            MAX(opened_at) AS latest_opened_at
        FROM position_lots
        WHERE status = 'OPEN'
          AND COALESCE(qty_remaining, 0) > 0
        GROUP BY symbol, side
        ORDER BY latest_opened_at DESC, symbol ASC, side ASC
        """
    ).fetchall()

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame([dict(r) for r in rows])

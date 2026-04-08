from __future__ import annotations


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
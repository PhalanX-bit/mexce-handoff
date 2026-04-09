from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.symbol_utils import canonical_futures_symbol

DB = ROOT / "data" / "mexc.sqlite"


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def normalize_table_symbols(con, table: str) -> tuple[int, list[dict]]:
    rows = con.execute(
        f"""
        SELECT id, symbol
        FROM {table}
        WHERE symbol IS NOT NULL
          AND TRIM(symbol) <> ''
        ORDER BY id ASC
        """
    ).fetchall()

    updates: list[dict] = []
    for row in rows:
        old_symbol = str(row["symbol"] or "").strip()
        new_symbol = canonical_futures_symbol(old_symbol)
        if not new_symbol or new_symbol == old_symbol:
            continue
        updates.append(
            {
                "id": int(row["id"]),
                "old_symbol": old_symbol,
                "new_symbol": str(new_symbol),
            }
        )

    for item in updates:
        con.execute(
            f"""
            UPDATE {table}
            SET symbol = ?
            WHERE id = ?
            """,
            (item["new_symbol"], int(item["id"])),
        )

    return len(updates), updates


def main() -> None:
    apply = "--apply" in sys.argv[1:]

    con = connect()
    try:
        total_position, updates_position = normalize_table_symbols(con, "position_lots")
        total_realizations, updates_realizations = normalize_table_symbols(con, "lot_realizations")

        summary = {
            "db": str(DB),
            "mode": "apply" if apply else "dry_run",
            "position_lots_updates": int(total_position),
            "lot_realizations_updates": int(total_realizations),
            "position_lots_preview": updates_position[:20],
            "lot_realizations_preview": updates_realizations[:20],
        }

        if not apply:
            con.rollback()
            print(summary)
            return

        con.commit()
        print(summary)
    finally:
        con.close()


if __name__ == "__main__":
    main()

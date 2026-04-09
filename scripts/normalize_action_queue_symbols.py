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


def collect_updates(con) -> list[dict]:
    rows = con.execute(
        """
        SELECT id, symbol
        FROM action_queue
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
    return updates


def main() -> None:
    apply = "--apply" in sys.argv[1:]

    con = connect()
    try:
        updates = collect_updates(con)
        summary = {
            "db": str(DB),
            "mode": "apply" if apply else "dry_run",
            "action_queue_updates": int(len(updates)),
            "preview": updates[:20],
        }

        if not apply:
            print(summary)
            return

        for item in updates:
            con.execute(
                """
                UPDATE action_queue
                SET symbol = ?
                WHERE id = ?
                """,
                (item["new_symbol"], int(item["id"])),
            )

        con.commit()
        print(summary)
    finally:
        con.close()


if __name__ == "__main__":
    main()

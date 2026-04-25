from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "mexc.sqlite"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def main() -> None:
    from core.streamlit_services.pending_chase_service import normalize_pending_chase_task_symbols
    from core.streamlit_services.pending_limit_service import normalize_pending_limit_task_symbols

    con = connect()
    try:
        pending_limit_updated = normalize_pending_limit_task_symbols(con)
        pending_chase_updated = normalize_pending_chase_task_symbols(con)
        print(
            {
                "db": str(DB),
                "pending_limit_updated": int(pending_limit_updated),
                "pending_chase_updated": int(pending_chase_updated),
            }
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()

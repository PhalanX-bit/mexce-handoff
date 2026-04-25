import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "mexc.sqlite"

# ==== CONFIG ====
MODE = "by_action_ids"
# options:
# "by_action_ids"
# "by_symbol"
# "resolved_only"

ACTION_IDS = [189, 190]
SYMBOL = "SOL/USDT"
# ================


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def print_rows(title, rows):
    print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return
    for r in rows:
        print(dict(r))


def main():
    con = connect()
    print(f"DB: {DB}")
    print(f"MODE: {MODE}")

    before = con.execute(
        """
        SELECT id, action_id, symbol, side, panel_mode, limit_price, qty, status,
               trigger_seen, baseline_contracts, triggered_at,
               attempt_count, created_at, updated_at, resolved_at, note
        FROM pending_limit_tasks
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()
    print_rows("pending_limit_tasks BEFORE", before)

    deleted = 0

    if MODE == "by_action_ids":
        if not ACTION_IDS:
            raise ValueError("ACTION_IDS is empty")
        placeholders = ",".join("?" for _ in ACTION_IDS)
        cur = con.execute(
            f"""
            DELETE FROM pending_limit_tasks
            WHERE action_id IN ({placeholders})
            """,
            ACTION_IDS,
        )
        deleted = cur.rowcount

    elif MODE == "by_symbol":
        cur = con.execute(
            """
            DELETE FROM pending_limit_tasks
            WHERE symbol = ?
            """,
            (SYMBOL,),
        )
        deleted = cur.rowcount

    elif MODE == "resolved_only":
        cur = con.execute(
            """
            DELETE FROM pending_limit_tasks
            WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
            """
        )
        deleted = cur.rowcount

    else:
        raise ValueError(f"Unknown MODE: {MODE}")

    con.commit()
    print(f"\nDeleted rows: {deleted}")

    after = con.execute(
        """
        SELECT id, action_id, symbol, side, panel_mode, limit_price, qty, status,
               trigger_seen, baseline_contracts, triggered_at,
               attempt_count, created_at, updated_at, resolved_at, note
        FROM pending_limit_tasks
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall()
    print_rows("pending_limit_tasks AFTER", after)

    con.close()


if __name__ == "__main__":
    main()
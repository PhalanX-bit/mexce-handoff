from pathlib import Path
import sqlite3

DB_PATH = Path("data/mexc.sqlite")

NEEDED_COLS = {
    "panel_mode": "TEXT NOT NULL DEFAULT 'CLOSE'",
    "order_kind": "TEXT NOT NULL DEFAULT 'POST_ONLY'",
    "limit_price": "REAL",
    "leverage": "INTEGER",
    "reduce_only": "INTEGER NOT NULL DEFAULT 1",
    "trigger_type": "TEXT NOT NULL DEFAULT 'manual'",
    "trigger_op": "TEXT",
    "trigger_price": "REAL",
    "priority": "INTEGER NOT NULL DEFAULT 100",
    "idempotency_key": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "last_error": "TEXT",
    "last_update_at": "TEXT",
    "note": "TEXT",
    # keep existing columns you already have (created_at, created_by, symbol, side, status, id, qty, etc.)
}

def get_existing_cols(con):
    rows = con.execute("PRAGMA table_info(action_queue)").fetchall()
    return {r[1] for r in rows}  # name is index 1

def main():
    con = sqlite3.connect(DB_PATH)
    existing = get_existing_cols(con)

    for col, ddl in NEEDED_COLS.items():
        if col not in existing:
            sql = f"ALTER TABLE action_queue ADD COLUMN {col} {ddl};"
            print("Applying:", sql)
            con.execute(sql)

    # Ensure indexes exist
    con.execute("CREATE INDEX IF NOT EXISTS idx_action_queue_status_prio ON action_queue(status, priority, id);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_action_queue_symbol ON action_queue(symbol);")

    con.commit()
    con.close()
    print("Migration applied OK.")

if __name__ == "__main__":
    main()

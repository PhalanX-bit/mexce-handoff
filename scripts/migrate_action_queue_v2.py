# scripts/migrate_action_queue_v2.py
from pathlib import Path
import sqlite3

DB_PATH = Path("data/mexc.sqlite")

SQL = """
CREATE TABLE IF NOT EXISTS action_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,

  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'streamlit',

  symbol TEXT NOT NULL,                  -- 'ADA/USDT'
  panel_mode TEXT NOT NULL,              -- 'OPEN' | 'CLOSE'
  side TEXT NOT NULL,                    -- 'LONG' | 'SHORT'

  order_kind TEXT NOT NULL,              -- 'LIMIT' | 'MARKET' | 'POST_ONLY' | 'CHASE_LIMIT' | 'TRIGGER' | 'TRAILING_STOP'
  qty REAL NOT NULL,
  limit_price REAL,                      -- used for LIMIT/POST_ONLY (optional if we auto-fill from bid/ask)

  leverage INTEGER,
  reduce_only INTEGER NOT NULL DEFAULT 1,

  trigger_type TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'immediate' | 'price'
  trigger_op TEXT,
  trigger_price REAL,

  status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | ARMED | RUNNING | DONE | FAILED | CANCELED
  priority INTEGER NOT NULL DEFAULT 100,
  idempotency_key TEXT UNIQUE,

  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_update_at TEXT,

  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_action_queue_status_prio ON action_queue(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_action_queue_symbol ON action_queue(symbol);
"""

def main():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SQL)
    con.commit()
    con.close()
    print("action_queue v2 migration applied OK")

if __name__ == "__main__":
    main()

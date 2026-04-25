from pathlib import Path
import sqlite3

DB_PATH = Path("data/mexc.sqlite")

SQL = """
CREATE TABLE IF NOT EXISTS action_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,

  created_at TEXT NOT NULL,         -- UTC ISO timestamp
  created_by TEXT NOT NULL,         -- 'streamlit' (future: 'system', 'manual', etc.)

  exchange TEXT NOT NULL,           -- 'mexc'
  market_type TEXT NOT NULL,        -- 'swap' (future: 'spot')
  symbol TEXT NOT NULL,             -- 'ADA/USDT'

  intent TEXT NOT NULL,             -- TRIM, CLOSE, OPEN, HEDGE, SETTING
  side TEXT,                        -- LONG/SHORT (required for position actions)
  reduce_only INTEGER NOT NULL DEFAULT 1,   -- 1 = reduce-only

  qty REAL NOT NULL,                -- contracts/size (as shown in UI)
  qty_unit TEXT NOT NULL DEFAULT 'contracts',  -- 'contracts' (future: 'usdt', 'coin')

  order_type TEXT NOT NULL DEFAULT 'market',    -- 'market' or 'limit'
  limit_price REAL,                 -- required if order_type='limit'
  slippage_bps INTEGER,             -- for market (e.g. 10 = 0.10%), optional

  -- When to execute (simple + robust)
  trigger_type TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'price' | 'immediate'
  trigger_op TEXT,                  -- '<=' | '>=' (only if trigger_type='price')
  trigger_price REAL,               -- price threshold
  trigger_timeout_sec INTEGER,      -- optional

  -- Safety gates (Tampermonkey must check before clicking)
  min_free_margin REAL,             -- if free margin < this => don't execute
  max_margin_ratio REAL,            -- if margin_ratio > this => don't execute (if available)
  max_spread_bps INTEGER,           -- optional UI spread check

  -- Execution bookkeeping
  status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | ARMED | RUNNING | DONE | FAILED | CANCELED | EXPIRED
  priority INTEGER NOT NULL DEFAULT 100,   -- lower = earlier
  idempotency_key TEXT UNIQUE,      -- to prevent duplicates (we generate it)

  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_update_at TEXT,

  ui_hint TEXT,                     -- free text: e.g. "Close in positions table row..."
  note TEXT                         -- human note
);

CREATE INDEX IF NOT EXISTS idx_action_queue_status_prio ON action_queue(status, priority, id);
CREATE INDEX IF NOT EXISTS idx_action_queue_symbol ON action_queue(symbol);
"""

def main():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SQL)
    con.commit()
    con.close()
    print("Migration v4 applied OK")

if __name__ == "__main__":
    main()

from pathlib import Path
import sqlite3

DB_PATH = Path("data/mexc.sqlite")

SQL = """
CREATE TABLE IF NOT EXISTS actions_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  action_type TEXT NOT NULL,      -- e.g. TRIM_LONG, TRIM_SHORT, HEDGE_OPEN, HEDGE_CLOSE, NOTE
  side TEXT,                      -- LONG/SHORT (optional)
  qty REAL,                       -- contracts/size (optional)
  price REAL,                     -- execution price if known (optional)
  note TEXT                       -- free text
);

CREATE INDEX IF NOT EXISTS idx_actions_created_at ON actions_ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_actions_symbol ON actions_ledger(symbol);
"""

def main():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SQL)
    con.commit()
    con.close()
    print("Migration v3 applied OK")

if __name__ == "__main__":
    main()

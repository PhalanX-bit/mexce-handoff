from pathlib import Path
import sqlite3

DB_PATH = Path("data/mexc.sqlite")

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS account_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  equity REAL,
  free_margin REAL,
  margin_ratio REAL
);

CREATE TABLE IF NOT EXISTS positions_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,           -- LONG / SHORT
  contracts REAL,
  entry_price REAL,
  unrealized_pnl REAL
);

CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exchange_trade_id TEXT,
  created_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,           -- buy/sell
  price REAL,
  amount REAL,
  fee REAL
);

CREATE INDEX IF NOT EXISTS idx_positions_snapshot_created_at ON positions_snapshot(created_at);
CREATE INDEX IF NOT EXISTS idx_positions_snapshot_symbol ON positions_snapshot(symbol);

CREATE INDEX IF NOT EXISTS idx_account_snapshot_created_at ON account_snapshot(created_at);

CREATE INDEX IF NOT EXISTS idx_fills_created_at ON fills(created_at);
CREATE INDEX IF NOT EXISTS idx_fills_symbol ON fills(symbol);
CREATE INDEX IF NOT EXISTS idx_fills_trade_id ON fills(exchange_trade_id);
"""

def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(MIGRATION_SQL)
    con.commit()
    con.close()
    print("Migration v1 applied OK")

if __name__ == "__main__":
    main()

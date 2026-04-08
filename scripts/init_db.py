from pathlib import Path
import sqlite3
from datetime import datetime

DB_PATH = Path("data/mexc.sqlite")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS engine_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  updated_at TEXT NOT NULL,
  global_mode TEXT NOT NULL,
  active_symbol TEXT NOT NULL,
  allowed INTEGER NOT NULL,
  step_pct REAL NOT NULL,
  size_mult REAL NOT NULL,
  max_net_expo_pct REAL NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS symbols_state (
  symbol TEXT PRIMARY KEY,
  updated_at TEXT NOT NULL,
  is_exhausted INTEGER NOT NULL,
  liveness_score REAL NOT NULL,
  avg_daily_range_pct REAL NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS events_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

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

"""

def now():
    return datetime.utcnow().isoformat(timespec="seconds")

def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA_SQL)

    # seed engine_state
    cur = con.execute("SELECT COUNT(*) FROM engine_state WHERE id=1")
    exists = cur.fetchone()[0] == 1
    if not exists:
        con.execute("""
        INSERT INTO engine_state (id, updated_at, global_mode, active_symbol, allowed, step_pct, size_mult, max_net_expo_pct, note)
        VALUES (1, ?, 'OFF', 'ADAUSDT', 0, 0.02, 1.0, 0.40, 'Seed')
        """, (now(),))

    # seed some symbols
    for sym in ["ADAUSDT", "SOLUSDT", "BTCUSDT", "ETHUSDT"]:
        con.execute("""
        INSERT OR IGNORE INTO symbols_state (symbol, updated_at, is_exhausted, liveness_score, avg_daily_range_pct, note)
        VALUES (?, ?, 0, 0.0, 0.0, 'Seed')
        """, (sym, now()))

    con.commit()
    con.close()
    print(f"Initialized DB at {DB_PATH}")

if __name__ == "__main__":
    main()

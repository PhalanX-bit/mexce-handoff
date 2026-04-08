import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "mexc.sqlite"

SQL = """
CREATE TABLE IF NOT EXISTS ticker_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  last_price REAL,
  mark_price REAL
);
CREATE INDEX IF NOT EXISTS idx_ticker_symbol_created
ON ticker_snapshot(symbol, created_at);
"""

def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SQL)
    con.commit()
    con.close()
    print("OK: ticker_snapshot ready ->", DB)

if __name__ == "__main__":
    main()

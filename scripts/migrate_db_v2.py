from pathlib import Path
import sqlite3

DB_PATH = Path("data/mexc.sqlite")

SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_fills_trade_id ON fills(exchange_trade_id);
"""

def main():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SQL)
    con.commit()
    con.close()
    print("Migration v2 applied OK")

if __name__ == "__main__":
    main()

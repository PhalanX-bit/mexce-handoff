from pathlib import Path
import sqlite3

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "mexc.sqlite"

print("ROOT_DIR =", ROOT_DIR)
print("DB_PATH =", DB_PATH)
print("DB exists =", DB_PATH.exists())

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

print("\n=== symbols_state schema ===")
schema_rows = con.execute("PRAGMA table_info(symbols_state)").fetchall()
for r in schema_rows:
    print(dict(r))

print("\n=== latest symbols_state rows ===")
rows = con.execute("""
    SELECT *
    FROM symbols_state
    ORDER BY rowid DESC
    LIMIT 10
""").fetchall()

for r in rows:
    print(dict(r))

con.close()
from pathlib import Path
import sqlite3

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "mexc.sqlite"

print("ROOT_DIR =", ROOT_DIR)
print("DB_PATH =", DB_PATH)
print("DB exists =", DB_PATH.exists())

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

def get_columns(table_name: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}

existing = get_columns("symbols_state")
print("Before:", sorted(existing))

to_add = [
    ("exchange_symbol", "TEXT"),
    ("price_tick", "REAL"),
    ("qty_step", "REAL"),
    ("min_qty", "REAL"),
    ("price_precision", "INTEGER"),
    ("qty_precision", "INTEGER"),
]

for col_name, col_type in to_add:
    if col_name not in existing:
        sql = f"ALTER TABLE symbols_state ADD COLUMN {col_name} {col_type}"
        print("Running:", sql)
        con.execute(sql)

con.commit()

updated = get_columns("symbols_state")
print("After:", sorted(updated))

print("\n=== symbols_state schema ===")
rows = con.execute("PRAGMA table_info(symbols_state)").fetchall()
for row in rows:
    print(dict(row))

con.close()
print("\nDone.")
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "mexc.sqlite"

LOT_ID = 7  # смени при нужда

con = sqlite3.connect(DB)
cur = con.execute("DELETE FROM position_lots WHERE id = ?", (LOT_ID,))
con.commit()

print(f"Deleted rows: {cur.rowcount}")

rows = con.execute(
    """
    SELECT id, symbol, side, qty_opened, qty_remaining, entry_price, status
    FROM position_lots
    ORDER BY id DESC
    LIMIT 10
    """
).fetchall()

for r in rows:
    print(r)

con.close()
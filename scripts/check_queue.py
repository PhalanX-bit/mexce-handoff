import sqlite3

con = sqlite3.connect("data/mexc.sqlite")
rows = con.execute("SELECT id, symbol, status FROM action_queue ORDER BY id").fetchall()
print(rows)
con.close()

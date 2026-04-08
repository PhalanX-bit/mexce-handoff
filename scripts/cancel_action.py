import sqlite3

con = sqlite3.connect("data/mexc.sqlite")
con.execute("UPDATE action_queue SET status='CANCELED' WHERE id=1")
con.commit()
con.close()

print("Canceled id=1")

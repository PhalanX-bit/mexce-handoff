import sqlite3

con = sqlite3.connect("data/mexc.sqlite")
con.execute(
    "UPDATE action_queue SET status='ARMED', last_error=NULL WHERE status='RUNNING'"
)
con.commit()
con.close()

print("Reset RUNNING -> ARMED")

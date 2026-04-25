import sqlite3

con = sqlite3.connect("data/mexc.sqlite")

print("DB:", con.execute("PRAGMA database_list").fetchall())
print("\nTABLE INFO:")
for r in con.execute("PRAGMA table_info(action_queue)").fetchall():
    print(r)

print("\nINDEX LIST:")
indexes = con.execute("PRAGMA index_list(action_queue)").fetchall()
for idx in indexes:
    print(idx)

print("\nUNIQUE INDEX DETAILS:")
for idx in indexes:
    # idx = (seq, name, unique, origin, partial)
    if idx[2] == 1:  # unique
        name = idx[1]
        cols = con.execute(f"PRAGMA index_info({name})").fetchall()
        print(name, "->", cols)

print("\nTRIGGERS:")
for r in con.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='action_queue'"
).fetchall():
    print(r)

con.close()

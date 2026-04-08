import sqlite3
from pathlib import Path

DB_PATH = Path("data/mexc.sqlite")

def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Normalize symbol columns by removing anything after ':'
    cur.execute("""
        UPDATE positions_snapshot
        SET symbol = substr(symbol, 1, instr(symbol, ':') - 1)
        WHERE instr(symbol, ':') > 0
    """)

    cur.execute("""
        UPDATE actions_ledger
        SET symbol = substr(symbol, 1, instr(symbol, ':') - 1)
        WHERE instr(symbol, ':') > 0
    """)

    # If you still have fills table (even if disabled), normalize it too
    try:
        cur.execute("""
            UPDATE fills
            SET symbol = substr(symbol, 1, instr(symbol, ':') - 1)
            WHERE instr(symbol, ':') > 0
        """)
    except Exception:
        pass

    con.commit()
    con.close()
    print("Cleanup OK")

if __name__ == "__main__":
    main()

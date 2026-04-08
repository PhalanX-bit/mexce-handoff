import sqlite3
from pathlib import Path

DB_PATH = Path("data/mexc.sqlite")

def connect():
    # check_same_thread=False helps if Streamlit reruns; WAL enabled in init
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

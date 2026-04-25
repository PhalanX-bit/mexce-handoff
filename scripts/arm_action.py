from pathlib import Path
import sqlite3
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "mexc.sqlite"

ACTION_ID = 245
LEVERAGE = 500

def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

con = sqlite3.connect(DB_PATH)
con.execute(
    """
    UPDATE action_queue
    SET status = 'ARMED',
        leverage = ?,
        last_error = NULL,
        api_order_id = NULL,
        api_client_oid = NULL,
        api_submit_path = NULL,
        api_mode = NULL,
        api_response = NULL,
        last_update_at = ?
    WHERE id = ?
    """,
    (LEVERAGE, utc_now_iso(), ACTION_ID),
)
con.commit()

row = con.execute(
    """
    SELECT id, status, symbol, side, panel_mode, qty, limit_price, leverage
    FROM action_queue
    WHERE id = ?
    """,
    (ACTION_ID,),
).fetchone()

print(row)
con.close()
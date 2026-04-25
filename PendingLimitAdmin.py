import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(r"C:\Users\DADDY\PycharmProjects\Funding_strategies\MEXC_anal_GPT\mexce\data\mexc.sqlite")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def list_tasks(limit: int = 20) -> None:
    con = connect()
    try:
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, limit_price, qty, status,
                   trigger_seen, baseline_contracts, triggered_at,
                   attempt_count, created_at, updated_at, resolved_at, note
            FROM pending_limit_tasks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not rows:
            print("No rows.")
            return

        for r in rows:
            print(dict(r))
    finally:
        con.close()


def reset_task(task_id: int) -> None:
    con = connect()
    try:
        cur = con.execute(
            """
            UPDATE pending_limit_tasks
            SET status='PENDING',
                trigger_seen=0,
                baseline_contracts=NULL,
                triggered_at=NULL,
                resolved_at=NULL,
                attempt_count=0,
                updated_at=datetime('now'),
                created_at=datetime('now'),
                note='RESET_FOR_TEST'
            WHERE id=?
            """,
            (task_id,),
        )
        con.commit()
        print(f"reset updated={cur.rowcount}")
    finally:
        con.close()


def delete_resolved() -> None:
    con = connect()
    try:
        cur = con.execute(
            """
            DELETE FROM pending_limit_tasks
            WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
            """
        )
        con.commit()
        print(f"deleted={cur.rowcount}")
    finally:
        con.close()


def set_created_now(task_id: int) -> None:
    con = connect()
    try:
        cur = con.execute(
            """
            UPDATE pending_limit_tasks
            SET created_at=datetime('now'),
                updated_at=datetime('now')
            WHERE id=?
            """,
            (task_id,),
        )
        con.commit()
        print(f"set_created_now updated={cur.rowcount}")
    finally:
        con.close()


def show_one(task_id: int) -> None:
    con = connect()
    try:
        row = con.execute(
            """
            SELECT id, action_id, symbol, side, limit_price, qty, status,
                   trigger_seen, baseline_contracts, triggered_at,
                   attempt_count, created_at, updated_at, resolved_at, note
            FROM pending_limit_tasks
            WHERE id=?
            """,
            (task_id,),
        ).fetchone()
        print(dict(row) if row else None)
    finally:
        con.close()


def usage() -> None:
    print("Usage:")
    print("  python pending_limit_admin.py list [limit]")
    print("  python pending_limit_admin.py show <id>")
    print("  python pending_limit_admin.py reset <id>")
    print("  python pending_limit_admin.py set_created_now <id>")
    print("  python pending_limit_admin.py delete_resolved")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
        raise SystemExit(1)

    cmd = sys.argv[1].lower()

    if cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        list_tasks(limit)
    elif cmd == "show":
        show_one(int(sys.argv[2]))
    elif cmd == "reset":
        reset_task(int(sys.argv[2]))
    elif cmd == "set_created_now":
        set_created_now(int(sys.argv[2]))
    elif cmd == "delete_resolved":
        delete_resolved()
    else:
        usage()
        raise SystemExit(1)

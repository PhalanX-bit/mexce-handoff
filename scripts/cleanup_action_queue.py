from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.reconcile_service import get_order_state_by_api_order_id
from core.reprice_service import normalize_api_order_id
from core.symbol_utils import canonical_futures_symbol

DB_PATH = ROOT_DIR / "data" / "mexc.sqlite"


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def is_live_open(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    symbol = canonical_futures_symbol(row.get("symbol")) or str(row.get("symbol") or "")
    api_order_id = normalize_api_order_id(row.get("api_order_id"))

    if not symbol or not api_order_id:
        return False, {
            "reason": "missing_symbol_or_api_order_id",
            "symbol": symbol,
            "api_order_id": api_order_id,
        }

    try:
        state = get_order_state_by_api_order_id(symbol, api_order_id)
    except Exception as exc:
        return False, {
            "reason": f"lookup_error:{type(exc).__name__}",
            "symbol": symbol,
            "api_order_id": api_order_id,
            "error": str(exc),
        }

    lifecycle_state = str(state.get("lifecycle_state") or "").upper()
    found_in_open_orders = bool(state.get("found_in_open_orders"))
    found_by_direct_open_lookup = bool(state.get("found_by_direct_open_lookup"))

    keep = lifecycle_state == "OPEN" or found_in_open_orders or found_by_direct_open_lookup
    return keep, state


def main() -> None:
    print(f"ROOT_DIR = {ROOT_DIR}")
    print(f"DB_PATH = {DB_PATH}")
    print(f"DB exists = {DB_PATH.exists()}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT *
            FROM action_queue
            ORDER BY id ASC
            """
        ).fetchall()

        keep_ids: list[int] = []
        delete_ids: list[int] = []

        summary = {
            "keep_pending": 0,
            "keep_armed": 0,
            "keep_running": 0,
            "keep_done_open": 0,
            "delete_done_not_open": 0,
            "delete_failed": 0,
            "delete_canceled": 0,
            "delete_other": 0,
        }

        print("\n=== REVIEW ===")
        for raw in rows:
            row = row_to_dict(raw) or {}
            action_id = int(row["id"])
            status = str(row.get("status") or "").upper()

            if status in {"PENDING", "ARMED", "RUNNING"}:
                keep_ids.append(action_id)
                if status == "PENDING":
                    summary["keep_pending"] += 1
                elif status == "ARMED":
                    summary["keep_armed"] += 1
                else:
                    summary["keep_running"] += 1
                print(f"KEEP   id={action_id} status={status}")
                continue

            if status in {"FAILED", "CANCELED"}:
                delete_ids.append(action_id)
                if status == "FAILED":
                    summary["delete_failed"] += 1
                else:
                    summary["delete_canceled"] += 1
                print(f"DELETE id={action_id} status={status}")
                continue

            if status == "DONE":
                keep, state = is_live_open(row)
                if keep:
                    keep_ids.append(action_id)
                    summary["keep_done_open"] += 1
                    print(
                        f"KEEP   id={action_id} status=DONE "
                        f"symbol={row.get('symbol')} api_order_id={row.get('api_order_id')} "
                        f"lifecycle_state={state.get('lifecycle_state')}"
                    )
                else:
                    delete_ids.append(action_id)
                    summary["delete_done_not_open"] += 1
                    print(
                        f"DELETE id={action_id} status=DONE "
                        f"symbol={row.get('symbol')} api_order_id={row.get('api_order_id')} "
                        f"reason={state.get('reason') or state.get('lifecycle_reason') or state.get('lifecycle_state')}"
                    )
                continue

            delete_ids.append(action_id)
            summary["delete_other"] += 1
            print(f"DELETE id={action_id} status={status or 'UNKNOWN'}")

        print("\n=== SUMMARY ===")
        for k, v in summary.items():
            print(f"{k} = {v}")

        print(f"\nWill keep   : {len(keep_ids)}")
        print(f"Will delete : {len(delete_ids)}")

        if not delete_ids:
            print("\nNothing to delete.")
            return

        confirm = input("\nType DELETE to confirm: ").strip()
        if confirm != "DELETE":
            print("Aborted.")
            return

        placeholders = ",".join("?" for _ in delete_ids)
        cur = conn.execute(
            f"""
            DELETE FROM action_queue
            WHERE id IN ({placeholders})
            """,
            delete_ids,
        )
        conn.commit()

        print(f"\nDeleted rows: {cur.rowcount}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
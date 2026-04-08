from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.action_queue_order import ACTION_QUEUE_EXECUTOR_ORDER_BY
from core.db import DB_PATH
from core.reprice_service import RepriceServiceConfig
from core.reprice_action_once_hardened import reprice_action_once_hardened
from core.symbol_utils import canonical_futures_symbol, symbols_match


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"))


def parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass
class RepriceWorkerConfig:
    service: RepriceServiceConfig
    statuses: tuple[str, ...] = ("DONE",)
    order_kinds: tuple[str, ...] = ("LIMIT", "POST_ONLY")
    max_items: int = 100
    poll_interval_sec: float = 2.0

    symbols: Optional[tuple[str, ...]] = None
    created_by_prefixes: Optional[tuple[str, ...]] = None
    only_reconcile_states: Optional[tuple[str, ...]] = None
    include_null_reconcile_state: bool = True
    min_created_at: Optional[str] = None
    max_age_hours: Optional[float] = None


def _fetch_candidates_raw(
    *,
    statuses: Sequence[str],
    order_kinds: Sequence[str],
    max_items: int,
) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        status_placeholders = ",".join("?" for _ in statuses)
        kind_placeholders = ",".join("?" for _ in order_kinds)

        sql = f"""
        SELECT *
        FROM action_queue
        WHERE status IN ({status_placeholders})
          AND order_kind IN ({kind_placeholders})
          AND api_order_id IS NOT NULL
        ORDER BY {ACTION_QUEUE_EXECUTOR_ORDER_BY}
        LIMIT ?
        """

        params = list(statuses) + list(order_kinds) + [int(max_items)]
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _match_scope_filters(
    row: Dict[str, Any],
    *,
    symbols: Optional[Sequence[str]],
    created_by_prefixes: Optional[Sequence[str]],
    only_reconcile_states: Optional[Sequence[str]],
    include_null_reconcile_state: bool,
    min_created_at: Optional[str],
    max_age_hours: Optional[float],
) -> bool:
    if symbols:
        row_symbol = row.get("symbol")
        if not any(symbols_match(row_symbol, s) for s in symbols):
            return False

    if created_by_prefixes:
        created_by = str(row.get("created_by") or "")
        if not any(created_by.startswith(prefix) for prefix in created_by_prefixes):
            return False

    if only_reconcile_states is not None:
        current_reconcile_state = row.get("reconcile_state")
        allowed = set(map(str, only_reconcile_states))

        if current_reconcile_state in (None, ""):
            if not include_null_reconcile_state:
                return False
        else:
            if str(current_reconcile_state) not in allowed:
                return False

    if min_created_at:
        row_created = parse_iso_utc(row.get("created_at"))
        min_dt = parse_iso_utc(min_created_at)
        if row_created is None or min_dt is None:
            return False
        if row_created < min_dt:
            return False

    if max_age_hours is not None:
        row_created = parse_iso_utc(row.get("created_at"))
        if row_created is None:
            return False
        max_age_delta = timedelta(hours=float(max_age_hours))
        if utc_now() - row_created > max_age_delta:
            return False

    return True


def list_reprice_candidates(
    *,
    config: RepriceWorkerConfig,
) -> List[Dict[str, Any]]:
    raw = _fetch_candidates_raw(
        statuses=config.statuses,
        order_kinds=config.order_kinds,
        max_items=config.max_items,
    )

    scoped = [
        row
        for row in raw
        if _match_scope_filters(
            row,
            symbols=config.symbols,
            created_by_prefixes=config.created_by_prefixes,
            only_reconcile_states=config.only_reconcile_states,
            include_null_reconcile_state=config.include_null_reconcile_state,
            min_created_at=config.min_created_at,
            max_age_hours=config.max_age_hours,
        )
    ]

    for row in scoped:
        row["canonical_symbol"] = canonical_futures_symbol(row.get("symbol"))

    return scoped


def run_reprice_pass(
    *,
    target_price_resolver: Callable[[Dict[str, Any]], Optional[float]],
    config: RepriceWorkerConfig,
    apply_changes: bool = True,
    verbose: bool = True,
) -> Dict[str, Any]:
    candidates = list_reprice_candidates(config=config)
    results: List[Dict[str, Any]] = []

    for row in candidates:
        action_id = int(row["id"])
        target_price = target_price_resolver(row)

        if target_price is None:
            result = {
                "ok": True,
                "stage": "skip",
                "reason": "missing_target_price",
                "action_id": action_id,
                "service_ts": utc_now_iso(),
                "symbol": row.get("symbol"),
                "canonical_symbol": row.get("canonical_symbol"),
            }
        else:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            try:
                result = reprice_action_once_hardened(
                    conn=conn,
                    action_id=action_id,
                    target_price=float(target_price),
                    config=config.service,
                    apply_changes=apply_changes,
                    require_order_open=bool(config.service.require_order_open),
                    lock_timeout_sec=90,
                    table_name="action_queue",
                )
            finally:
                conn.close()

        result["canonical_symbol"] = row.get("canonical_symbol")
        results.append(result)

        if verbose:
            print(safe_json(result))

    return {
        "ok": True,
        "stage": "pass_done",
        "apply_changes": bool(apply_changes),
        "candidates": len(candidates),
        "results": results,
        "service_ts": utc_now_iso(),
    }


def run_reprice_loop(
    *,
    target_price_resolver: Callable[[Dict[str, Any]], Optional[float]],
    config: RepriceWorkerConfig,
    apply_changes: bool = True,
    max_cycles: int = 10,
    verbose: bool = True,
) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = []

    for cycle_no in range(1, int(max_cycles) + 1):
        result = run_reprice_pass(
            target_price_resolver=target_price_resolver,
            config=config,
            apply_changes=apply_changes,
            verbose=verbose,
        )
        result["cycle_no"] = cycle_no
        history.append(result)

        if cycle_no < int(max_cycles):
            time.sleep(float(config.poll_interval_sec))

    return {
        "ok": True,
        "stage": "loop_done",
        "cycles_completed": int(max_cycles),
        "history": history,
        "service_ts": utc_now_iso(),
    }


if __name__ == "__main__":
    print(
        safe_json(
            {
                "ok": True,
                "stage": "module_loaded",
                "db_path": str(Path(DB_PATH).resolve()),
            }
        )
    )

# app/streamlit_app.py
# FULL FILE — Strategy v1.1
# - Hedge OPEN via OPEN+CHASE
# - Profit trim via CLOSE+LIMIT
# - Trim trigger is based on entry price + target ROI at leverage
# - Submit LIMIT price is based on current last_price with small offset

import sys
from pathlib import Path
from datetime import datetime, timezone
import uuid
import time
from typing import Optional

import pandas as pd
import streamlit as st

# Ensure project root on sys.path so "core" imports work
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db import connect  # noqa: E402


# ---------- Helpers ----------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_float_series(df: pd.DataFrame, col: str) -> None:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)


def risk_flag(row, equity):
    gross = abs(float(row.get("gross_contracts") or 0.0))
    net = abs(float(row.get("net_contracts") or 0.0))

    if gross == 0:
        return "—"

    if net == gross:
        return "🔴 HIGH RISK – REDUCE ONLY"

    hedge_ratio = 1 - (net / gross)

    if hedge_ratio >= 0.75:
        return "🟢 HARVEST"
    if hedge_ratio >= 0.40:
        return "🟡 CONTROL"
    return "🟠 CAUTION"


def suggest_next_action(row):
    gross = float(row.get("gross_contracts") or 0.0)
    net = float(row.get("net_contracts") or 0.0)
    flag = str(row.get("risk_flag") or "")

    if gross == 0:
        return "No position."

    if abs(net) == abs(gross):
        side = "SHORT" if net < 0 else "LONG"
        return (
            f"One-sided {side}. Reduce only: close 10–15% on the next favorable move. "
            f"No adds; emergency hedge only if margin becomes tight."
        )

    hedge_ratio = 1 - (abs(net) / abs(gross)) if gross else 0.0

    if "HARVEST" in flag or hedge_ratio >= 0.75:
        return (
            "Hedged. Harvest oscillation: trim 5–10% from the profitable leg on a spike, "
            "then wait. Goal: reduce gross gradually while keeping net small."
        )

    if "CONTROL" in flag or hedge_ratio >= 0.40:
        return (
            "Partially hedged. Prefer trims over adds: trim the profitable side 5–10% at relief moves. "
            "Avoid increasing gross."
        )

    return (
        "Caution. Net exposure is relatively large: prioritize gross reduction. "
        "Trim profitable side first; avoid adding into moves."
    )


# ---------- Pending LIMIT helpers ----------
def get_pending_limit_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pending_limit_tasks
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "TRIGGERED": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "FAILED_AFTER_TRIGGER": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def list_pending_limit_tasks(con, status: str = "ALL", limit: int = 200):
    if status == "ALL":
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, limit_price, qty, status,
                   trigger_seen, baseline_contracts, triggered_at,
                   attempt_count, created_at, updated_at, resolved_at, note
            FROM pending_limit_tasks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, limit_price, qty, status,
                   trigger_seen, baseline_contracts, triggered_at,
                   attempt_count, created_at, updated_at, resolved_at, note
            FROM pending_limit_tasks
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return rows


def reset_pending_limit_task(con, task_id: int) -> int:
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
    return cur.rowcount


def set_pending_limit_created_now(con, task_id: int) -> int:
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
    return cur.rowcount


def delete_resolved_pending_limit_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
        """
    )
    con.commit()
    return cur.rowcount


def delete_pending_limit_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_limit_tasks
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def mark_pending_limit_task_status(con, task_id: int, new_status: str, note_suffix: str = "") -> int:
    cur = con.execute(
        """
        UPDATE pending_limit_tasks
        SET status=?,
            updated_at=datetime('now'),
            resolved_at=CASE
                WHEN ? IN ('FILLED', 'EXPIRED', 'FAILED_AFTER_TRIGGER')
                THEN datetime('now')
                ELSE resolved_at
            END,
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (new_status, new_status, note_suffix, task_id),
    )
    con.commit()
    return cur.rowcount


# ---------- Pending CHASE helpers ----------
def get_pending_chase_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM pending_chase_tasks
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "FILLED": 0,
        "EXPIRED": 0,
        "FAILED": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def list_pending_chase_tasks(con, status: str = "ALL", limit: int = 200):
    if status == "ALL":
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, qty, status,
                   baseline_contracts, filled_contracts,
                   created_at, updated_at, resolved_at, note
            FROM pending_chase_tasks
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT id, action_id, symbol, side, panel_mode, qty, status,
                   baseline_contracts, filled_contracts,
                   created_at, updated_at, resolved_at, note
            FROM pending_chase_tasks
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return rows


def reset_pending_chase_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET status='PENDING',
            baseline_contracts=NULL,
            filled_contracts=NULL,
            resolved_at=NULL,
            updated_at=datetime('now'),
            created_at=datetime('now'),
            note='RESET_FOR_TEST'
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def set_pending_chase_created_now(con, task_id: int) -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET created_at=datetime('now'),
            updated_at=datetime('now')
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def delete_resolved_pending_chase_tasks(con) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_chase_tasks
        WHERE status IN ('FILLED', 'EXPIRED', 'FAILED')
        """
    )
    con.commit()
    return cur.rowcount


def delete_pending_chase_task(con, task_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM pending_chase_tasks
        WHERE id=?
        """,
        (task_id,),
    )
    con.commit()
    return cur.rowcount


def mark_pending_chase_task_status(con, task_id: int, new_status: str, note_suffix: str = "") -> int:
    cur = con.execute(
        """
        UPDATE pending_chase_tasks
        SET status=?,
            updated_at=datetime('now'),
            resolved_at=CASE
                WHEN ? IN ('FILLED', 'EXPIRED', 'FAILED')
                THEN datetime('now')
                ELSE resolved_at
            END,
            note=COALESCE(note, '') || ?
        WHERE id=?
        """,
        (new_status, new_status, note_suffix, task_id),
    )
    con.commit()
    return cur.rowcount


# ---------- DB Readers/Writers ----------
def get_latest_account_snapshot(con):
    row = con.execute(
        """
        SELECT *
        FROM account_snapshot
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def get_latest_positions_per_symbol_side(con):
    rows = con.execute(
        """
        SELECT p.symbol, p.side, p.contracts, p.entry_price, p.unrealized_pnl, p.created_at
        FROM positions_snapshot p
        JOIN (
            SELECT symbol, side, MAX(id) AS max_id
            FROM positions_snapshot
            GROUP BY symbol, side
        ) latest
        ON p.id = latest.max_id
        ORDER BY p.symbol ASC, p.side ASC
        """
    ).fetchall()

    out = [dict(r) for r in rows]
    latest_ts = max((r.get("created_at") for r in out if r.get("created_at")), default=None)
    return latest_ts, out


def add_action_ledger(con, created_at, symbol, action_type, side=None, qty=None, price=None, note=None):
    con.execute(
        """
        INSERT INTO actions_ledger (created_at, symbol, action_type, side, qty, price, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, symbol, action_type, side, qty, price, note),
    )


def get_recent_actions(con, limit=50):
    rows = con.execute(
        """
        SELECT created_at, symbol, action_type, side, qty, price, note
        FROM actions_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_tickers(con):
    rows = con.execute(
        """
        SELECT t.symbol, t.last_price, t.mark_price, t.created_at
        FROM ticker_snapshot t
        JOIN (
            SELECT symbol, MAX(id) AS max_id
            FROM ticker_snapshot
            GROUP BY symbol
        ) latest
        ON t.id = latest.max_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_ticker_price_for_symbol(con, symbol: str) -> Optional[float]:
    row = con.execute(
        """
        SELECT last_price
        FROM ticker_snapshot
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    if not row:
        return None

    try:
        v = row["last_price"]
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


# ---------- Action Queue (v2) helpers ----------
def queue_insert_v2(con, p: dict) -> int:
    p = dict(p)
    p["idempotency_key"] = uuid.uuid4().hex

    cur = con.execute(
        """
        INSERT INTO action_queue (
          created_at, created_by, exchange, market_type, symbol, intent, side,
          reduce_only, qty, qty_unit, order_type, limit_price, slippage_bps,
          trigger_type, trigger_op, trigger_price, trigger_timeout_sec,
          min_free_margin, max_margin_ratio, max_spread_bps,
          status, priority, idempotency_key, attempts, last_error, last_update_at,
          ui_hint, note, panel_mode, order_kind, leverage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["created_at"],
            p.get("created_by", "streamlit"),
            p.get("exchange", "mexc"),
            p.get("market_type", "swap"),
            p["symbol"],
            p.get("intent", "close"),
            p.get("side"),
            int(p.get("reduce_only", 1)),
            float(p["qty"]),
            p.get("qty_unit", "contracts"),
            p.get("order_type", "market"),
            p.get("limit_price"),
            p.get("slippage_bps"),
            p.get("trigger_type", "manual"),
            p.get("trigger_op"),
            p.get("trigger_price"),
            p.get("trigger_timeout_sec"),
            p.get("min_free_margin"),
            p.get("max_margin_ratio"),
            p.get("max_spread_bps"),
            p.get("status", "PENDING"),
            int(p.get("priority", 100)),
            p["idempotency_key"],
            int(p.get("attempts", 0)),
            p.get("last_error"),
            p.get("last_update_at"),
            p.get("ui_hint"),
            p.get("note"),
            p.get("panel_mode", "CLOSE"),
            p.get("order_kind", "POST_ONLY"),
            p.get("leverage"),
        ),
    )
    return int(cur.rowcount)


def queue_list_v2(con, status=None, limit=200):
    if status and status != "ALL":
        rows = con.execute(
            """
            SELECT *
            FROM action_queue
            WHERE status=?
            ORDER BY priority ASC, id ASC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT *
            FROM action_queue
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_action_queue_status_counts(con):
    rows = con.execute(
        """
        SELECT status, COUNT(*) AS cnt
        FROM action_queue
        GROUP BY status
        """
    ).fetchall()

    out = {
        "PENDING": 0,
        "ARMED": 0,
        "RUNNING": 0,
        "DONE": 0,
        "FAILED": 0,
        "CANCELED": 0,
    }

    for r in rows:
        out[str(r["status"]).upper()] = int(r["cnt"])

    return out


def queue_arm(con, action_id: int):
    con.execute(
        "UPDATE action_queue SET status='ARMED', last_update_at=? WHERE id=? AND status='PENDING'",
        (now_utc_iso(), action_id),
    )


def queue_cancel(con, action_id: int):
    con.execute(
        "UPDATE action_queue SET status='CANCELED', last_update_at=? WHERE id=? AND status IN ('PENDING','ARMED')",
        (now_utc_iso(), action_id),
    )


def delete_action_queue_by_statuses(con, statuses: list[str]) -> int:
    if not statuses:
        return 0

    placeholders = ",".join(["?"] * len(statuses))
    cur = con.execute(
        f"""
        DELETE FROM action_queue
        WHERE status IN ({placeholders})
        """,
        tuple(statuses),
    )
    con.commit()
    return int(cur.rowcount)


def delete_action_queue_row(con, action_id: int) -> int:
    cur = con.execute(
        """
        DELETE FROM action_queue
        WHERE id=?
        """,
        (action_id,),
    )
    con.commit()
    return int(cur.rowcount)


# ---------- Debug DB path ----------
def get_db_files(con):
    rows = con.execute("PRAGMA database_list;").fetchall()
    out = []
    for r in rows:
        try:
            out.append({"seq": r[0], "name": r[1], "file": r[2]})
        except Exception:
            out.append({"row": str(r)})
    return out


# ---------- Strategy helpers ----------
def has_active_task_for_symbol(con, symbol: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM action_queue
        WHERE symbol=?
          AND status IN ('ARMED','RUNNING')
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def has_active_pending_limit_for_symbol(con, symbol: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM pending_limit_tasks
        WHERE symbol=?
          AND status IN ('PENDING', 'TRIGGERED')
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def has_active_pending_chase_for_symbol(con, symbol: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM pending_chase_tasks
        WHERE symbol=?
          AND status = 'PENDING'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return row is not None


def enqueue_armed_open_chase(con, *, symbol: str, side: str, qty: float, note: str, priority: int = 50) -> None:
    p = {
        "created_at": now_utc_iso(),
        "created_by": "streamlit:auto",
        "exchange": "mexc",
        "market_type": "swap",
        "symbol": symbol.strip(),
        "intent": "open",
        "side": side.upper(),
        "reduce_only": 0,
        "qty": float(qty),
        "qty_unit": "contracts",
        "order_type": "market",
        "limit_price": None,
        "slippage_bps": None,
        "trigger_type": "immediate",
        "trigger_op": None,
        "trigger_price": None,
        "trigger_timeout_sec": None,
        "min_free_margin": None,
        "max_margin_ratio": None,
        "max_spread_bps": None,
        "status": "ARMED",
        "priority": int(priority),
        "attempts": 0,
        "last_error": None,
        "last_update_at": now_utc_iso(),
        "ui_hint": None,
        "note": note.strip() if note else None,
        "panel_mode": "OPEN",
        "order_kind": "CHASE",
        "leverage": None,
    }
    queue_insert_v2(con, p)
    con.commit()


def enqueue_armed_close_limit(
    con,
    *,
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
    note: str,
    priority: int = 40,
) -> None:
    p = {
        "created_at": now_utc_iso(),
        "created_by": "streamlit:auto",
        "exchange": "mexc",
        "market_type": "swap",
        "symbol": symbol.strip(),
        "intent": "close",
        "side": side.upper(),
        "reduce_only": 1,
        "qty": float(qty),
        "qty_unit": "contracts",
        "order_type": "limit",
        "limit_price": float(limit_price),
        "slippage_bps": None,
        "trigger_type": "immediate",
        "trigger_op": None,
        "trigger_price": None,
        "trigger_timeout_sec": None,
        "min_free_margin": None,
        "max_margin_ratio": None,
        "max_spread_bps": None,
        "status": "ARMED",
        "priority": int(priority),
        "attempts": 0,
        "last_error": None,
        "last_update_at": now_utc_iso(),
        "ui_hint": None,
        "note": note.strip() if note else None,
        "panel_mode": "CLOSE",
        "order_kind": "LIMIT",
        "leverage": None,
    }
    queue_insert_v2(con, p)
    con.commit()


def get_leg(df_pos: pd.DataFrame, symbol: str, side: str) -> dict:
    if df_pos.empty:
        return {"contracts": 0.0, "unrealized_pnl": 0.0, "entry_price": 0.0}

    x = df_pos[(df_pos["symbol"] == symbol) & (df_pos["side"] == side)]
    if x.empty:
        return {"contracts": 0.0, "unrealized_pnl": 0.0, "entry_price": 0.0}

    r = x.iloc[0].to_dict()
    return {
        "contracts": float(r.get("contracts") or 0.0),
        "unrealized_pnl": float(r.get("unrealized_pnl") or 0.0),
        "entry_price": float(r.get("entry_price") or 0.0),
    }


# ---------- Reset stuck tasks ----------
def reset_running(con, symbol: Optional[str] = None, also_reset_armed: bool = False) -> int:
    statuses = ["RUNNING"]
    if also_reset_armed:
        statuses.append("ARMED")

    if symbol:
        cur = con.execute(
            f"""
            UPDATE action_queue
            SET status='FAILED',
                last_error=COALESCE(last_error, 'manual reset (stuck)'),
                last_update_at=datetime('now')
            WHERE symbol=?
              AND status IN ({",".join(["?"] * len(statuses))})
            """,
            (symbol, *statuses),
        )
    else:
        cur = con.execute(
            f"""
            UPDATE action_queue
            SET status='FAILED',
                last_error=COALESCE(last_error, 'manual reset (stuck)'),
                last_update_at=datetime('now')
            WHERE status IN ({",".join(["?"] * len(statuses))})
            """,
            (*statuses,),
        )
    con.commit()
    return int(cur.rowcount)


# ---------- UI ----------
st.set_page_config(page_title="MEXC Monitor", layout="wide")
st.title("MEXC Monitor (read-only)")

con = connect()

with st.sidebar:
    st.subheader("Debug")
    if st.checkbox("Show DB file path(s)", value=True):
        st.write(get_db_files(con))

    st.divider()
    st.subheader("Emergency tools (DB)")
    st.caption("Use only if tasks are stuck RUNNING and block the Strategy guard.")
    sym_reset = st.text_input("Symbol to reset (optional)", value="").strip()
    also_armed = st.checkbox("Also reset ARMED", value=False)

    c1, c2 = st.columns(2)
    if c1.button("Reset RUNNING (symbol)"):
        if not sym_reset:
            st.error("Enter a symbol (e.g. ADA/USDT).")
        else:
            n = reset_running(con, symbol=sym_reset, also_reset_armed=also_armed)
            st.success(f"Reset {n} rows for {sym_reset}.")
            st.rerun()

    if c2.button("Reset RUNNING (ALL)"):
        n = reset_running(con, symbol=None, also_reset_armed=also_armed)
        st.success(f"Reset {n} rows (ALL symbols).")
        st.rerun()


tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Dashboard",
    "Action Queue",
    "Action Ledger",
    "Strategy (AUTO)",
    "Pending LIMIT",
    "Pending CHASE",
])

# =================== Dashboard ===================
with tab1:
    st.subheader("Live data (collector snapshots)")

    cA, cB, cC = st.columns([1, 1, 2])
    show_closed_legs = cA.checkbox("Show CLOSED legs (contracts=0)", value=False)
    aggregates_open_only = cB.checkbox("Aggregates from OPEN legs only", value=True)
    cC.caption("If a leg is closed, collector should write a latest row with contracts=0.")

    acc = get_latest_account_snapshot(con)
    pos_ts, pos_rows = get_latest_positions_per_symbol_side(con)

    c1, c2, c3, c4 = st.columns(4)
    if acc:
        c1.metric("Equity (USDT)", f"{acc['equity']:.4f}" if acc.get("equity") is not None else "—")
        c2.metric("Free margin (USDT)", f"{acc['free_margin']:.4f}" if acc.get("free_margin") is not None else "—")
        c3.metric("Margin ratio", f"{acc['margin_ratio']:.4f}" if acc.get("margin_ratio") is not None else "—")
        c4.metric("Account snapshot (UTC)", acc.get("created_at", "—"))
    else:
        c1.metric("Equity (USDT)", "—")
        c2.metric("Free margin (USDT)", "—")
        c3.metric("Margin ratio", "—")
        c4.metric("Account snapshot (UTC)", "—")

    st.caption(f"Latest positions per (symbol, side) timestamp: {pos_ts or '—'}")

    if pos_rows:
        df_pos = pd.DataFrame(pos_rows)

        _to_float_series(df_pos, "contracts")
        _to_float_series(df_pos, "entry_price")
        _to_float_series(df_pos, "unrealized_pnl")

        tick_rows = get_latest_tickers(con)
        if tick_rows:
            df_tick = pd.DataFrame(tick_rows)
            _to_float_series(df_tick, "last_price")
            _to_float_series(df_tick, "mark_price")

            df_pos = df_pos.merge(
                df_tick[["symbol", "last_price", "mark_price", "created_at"]].rename(
                    columns={"created_at": "ticker_at"}
                ),
                on="symbol",
                how="left",
            )

        df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
        df_pos["status"] = df_pos["contracts"].apply(lambda x: "OPEN" if float(x or 0.0) > 0 else "CLOSED")

        df_pos_view = df_pos[df_pos["status"] == "OPEN"].copy() if not show_closed_legs else df_pos.copy()

        df_pos_view["signed_contracts"] = df_pos_view.apply(
            lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
            axis=1,
        )

        df_for_agg = df_pos[df_pos["status"] == "OPEN"].copy() if aggregates_open_only else df_pos.copy()

        df_for_agg["signed_contracts"] = df_for_agg.apply(
            lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
            axis=1,
        )

        df_agg = (
            df_for_agg.groupby("symbol", as_index=False)
            .agg(
                total_unrealized=("unrealized_pnl", "sum"),
                net_contracts=("signed_contracts", "sum"),
                gross_contracts=("contracts", "sum"),
            )
            .sort_values("total_unrealized")
        )

        equity_val = acc.get("equity") if acc and acc.get("equity") else None
        df_agg["risk_flag"] = df_agg.apply(lambda r: risk_flag(r, equity_val), axis=1)
        df_agg["suggested_next_action"] = df_agg.apply(suggest_next_action, axis=1)

        left, right = st.columns([2, 1])

        with left:
            st.write("### Positions (latest per symbol+side)")
            cols_show = ["symbol", "side", "status", "contracts", "entry_price", "last_price", "unrealized_pnl", "created_at", "ticker_at"]
            st.dataframe(df_pos_view[cols_show], width="stretch")

        with right:
            st.write("### Aggregates by symbol (risk + suggestion)")
            st.dataframe(
                df_agg[
                    [
                        "symbol",
                        "risk_flag",
                        "gross_contracts",
                        "net_contracts",
                        "total_unrealized",
                        "suggested_next_action",
                    ]
                ],
                width="stretch",
            )

        losers = df_agg.nsmallest(5, "total_unrealized")
        winners = df_agg.nlargest(5, "total_unrealized")

        l1, l2 = st.columns(2)
        with l1:
            st.write("### Top losers (unrealized)")
            st.dataframe(losers, width="stretch")
        with l2:
            st.write("### Top winners (unrealized)")
            st.dataframe(winners, width="stretch")

        st.write("### Suggested next actions (per symbol)")
        for _, r in df_agg.iterrows():
            st.write(f"**{r['symbol']}** — {r['risk_flag']}")
            st.write(r["suggested_next_action"])
            st.write("")
    else:
        st.info("No positions_snapshot data yet. Run: python -m core.collector")


# =================== Action Queue ===================
with tab2:
    st.subheader("Action Queue (PENDING → ARM → Tampermonkey executes ARMED)")
    st.caption("Executor supports LIMIT/CHASE. Strategy uses OPEN+CHASE and CLOSE+LIMIT.")

    qcounts = get_action_queue_status_counts(con)

    q1, q2, q3, q4, q5, q6 = st.columns(6)
    q1.metric("PENDING", qcounts.get("PENDING", 0))
    q2.metric("ARMED", qcounts.get("ARMED", 0))
    q3.metric("RUNNING", qcounts.get("RUNNING", 0))
    q4.metric("DONE", qcounts.get("DONE", 0))
    q5.metric("FAILED", qcounts.get("FAILED", 0))
    q6.metric("CANCELED", qcounts.get("CANCELED", 0))

    with st.form("queue_create_v2", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        symbol = c1.text_input("Symbol", value="ADA/USDT")
        panel_mode = c2.selectbox("Panel mode", ["CLOSE", "OPEN"], index=0)
        side = c3.selectbox("Side", ["SHORT", "LONG"], index=0)
        qty = c4.number_input("Qty (Cont)", min_value=0.0, value=50.0, step=1.0)

        c5, c6, c7, c8 = st.columns(4)
        order_kind = c5.selectbox(
            "Order kind",
            ["LIMIT", "CHASE", "POST_ONLY", "MARKET", "CHASE_LIMIT", "TRIGGER", "TRAILING_STOP"],
            index=0,
        )
        limit_price = c6.number_input(
            "Limit price (USDT) (0 = none)",
            min_value=0.0,
            value=0.0,
            step=0.0001,
            format="%.6f",
        )
        reduce_only = c7.checkbox("Reduce-only", value=True)
        priority = c8.number_input("Priority (lower runs first)", min_value=0, value=100, step=1)

        c9, c10, c11 = st.columns(3)
        trigger_type = c9.selectbox("Trigger type", ["manual", "immediate", "price"], index=0)
        trigger_op = c10.selectbox("Trigger op", ["<=", ">="], index=0)
        trigger_price = c11.number_input(
            "Trigger price (0 = none)",
            min_value=0.0,
            value=0.0,
            step=0.0001,
            format="%.6f",
        )

        note = st.text_input("Note (optional)", value="")
        submit = st.form_submit_button("Create PENDING")

        if submit:
            if trigger_type == "price" and trigger_price <= 0:
                st.error("If trigger_type=price you must set trigger_price > 0.")
                st.stop()

            p = {
                "created_at": now_utc_iso(),
                "created_by": "streamlit",
                "exchange": "mexc",
                "market_type": "swap",
                "symbol": symbol.strip(),
                "intent": "close" if panel_mode == "CLOSE" else "open",
                "side": side,
                "reduce_only": 1 if reduce_only else 0,
                "qty": float(qty),
                "qty_unit": "contracts",
                "order_type": "limit" if order_kind in ("POST_ONLY", "LIMIT") else "market",
                "limit_price": float(limit_price) if limit_price > 0 else None,
                "trigger_type": trigger_type,
                "trigger_op": trigger_op if trigger_type == "price" else None,
                "trigger_price": float(trigger_price) if trigger_type == "price" else None,
                "status": "PENDING",
                "priority": int(priority),
                "note": note.strip() if note.strip() else None,
                "panel_mode": panel_mode,
                "order_kind": order_kind,
                "leverage": None,
                "last_update_at": now_utc_iso(),
            }

            inserted = queue_insert_v2(con, p)
            con.commit()
            if inserted == 1:
                st.success("Created PENDING action.")
            else:
                st.error("Insert failed (unexpected). Check logs.")

    st.divider()

    f1, f2, f3 = st.columns([1, 1, 1])
    status_filter = f1.selectbox(
        "Status filter",
        ["ALL", "PENDING", "ARMED", "RUNNING", "DONE", "FAILED", "CANCELED"],
        index=0,
    )
    limit = f2.number_input("Rows", min_value=20, value=200, step=20)
    if f3.button("Refresh table"):
        st.rerun()

    rows = queue_list_v2(con, status_filter, int(limit))
    if rows:
        dfq = pd.DataFrame(rows)
        cols = [
            "id",
            "created_at",
            "symbol",
            "panel_mode",
            "order_kind",
            "side",
            "qty",
            "limit_price",
            "trigger_type",
            "trigger_op",
            "trigger_price",
            "status",
            "priority",
            "attempts",
            "last_error",
            "last_update_at",
            "note",
        ]
        st.dataframe(dfq[[c for c in cols if c in dfq.columns]], width="stretch")

        st.write("### Controls")
        cA, cB, cC = st.columns(3)
        action_id = cA.number_input("Action ID", min_value=1, value=int(dfq["id"].iloc[0]), step=1)

        if cB.button("ARM selected (PENDING→ARMED)"):
            queue_arm(con, int(action_id))
            con.commit()
            st.success(f"ARMED {action_id}")
            st.rerun()

        if cC.button("CANCEL selected (PENDING/ARMED only)"):
            queue_cancel(con, int(action_id))
            con.commit()
            st.success(f"CANCELED {action_id}")
            st.rerun()

        cD, cE, cF = st.columns(3)

        if cD.button("Delete selected row"):
            n = delete_action_queue_row(con, int(action_id))
            if n:
                st.success(f"Deleted action_queue row id={int(action_id)}")
            else:
                st.warning("No row deleted.")
            st.rerun()

        if cE.button("Delete DONE/FAILED/CANCELED"):
            n = delete_action_queue_by_statuses(con, ["DONE", "FAILED", "CANCELED"])
            st.success(f"Deleted rows: {n}")
            st.rerun()

        if cF.button("Delete DONE only"):
            n = delete_action_queue_by_statuses(con, ["DONE"])
            st.success(f"Deleted DONE rows: {n}")
            st.rerun()

        st.caption("Tip: To clear RUNNING tasks, use the sidebar Emergency tools (Reset RUNNING).")
    else:
        st.info("No actions yet.")


# =================== Action Ledger ===================
with tab3:
    st.subheader("Action Ledger (manual notes / confirmations)")

    with st.form("add_action_form", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns(4)
        symbol_in = col1.text_input("Symbol", value="ADA/USDT")
        action_type_in = col2.selectbox("Action type", ["NOTE", "TRIM_LONG", "TRIM_SHORT", "HEDGE_OPEN", "HEDGE_CLOSE"])
        side_in = col3.selectbox("Side (optional)", ["", "LONG", "SHORT"])
        qty_in = col4.number_input("Qty (optional)", min_value=0.0, value=0.0, step=1.0)

        col5, col6 = st.columns(2)
        price_in = col5.number_input("Price (optional)", min_value=0.0, value=0.0, step=0.0001, format="%.6f")
        note_in = col6.text_input("Note (optional)", value="")

        submitted = st.form_submit_button("Add ledger entry")
        if submitted:
            add_action_ledger(
                con,
                created_at=now_utc_iso(),
                symbol=symbol_in.strip(),
                action_type=action_type_in,
                side=side_in if side_in else None,
                qty=qty_in if qty_in > 0 else None,
                price=price_in if price_in > 0 else None,
                note=note_in.strip() if note_in.strip() else None,
            )
            con.commit()
            st.success("Ledger entry saved.")
            st.rerun()

    actions = get_recent_actions(con, limit=50)
    if actions:
        df_actions = pd.DataFrame(actions)
        st.dataframe(df_actions, width="stretch")
    else:
        st.caption("No actions yet.")


# =================== Strategy (AUTO) ===================
with tab4:
    st.subheader("Strategy (AUTO) — Hedge Cycle v1.1")
    st.caption(
        "Initial/hedge entry uses OPEN+CHASE. Profit trim trigger uses entry-based ROI target. "
        "Submitted CLOSE+LIMIT price uses current last_price with a small offset."
    )

    try:
        from streamlit_autorefresh import st_autorefresh  # type: ignore
        autorefresh_available = True
    except Exception:
        autorefresh_available = False

    c1, c2, c3, c4 = st.columns(4)
    sym = c1.text_input("Symbol (strategy)", value="ADA/USDT").strip()
    initial_side = c2.selectbox("Initial side", ["LONG", "SHORT"], index=1)
    base_qty = c3.number_input("Base qty (contracts)", min_value=1.0, value=1.0, step=1.0)
    priority = c4.number_input("Priority (lower runs first)", min_value=0, value=50, step=1)

    d1, d2, d3, d4 = st.columns(4)
    hedge_loss_usdt = d1.number_input("Hedge trigger (uPnL <=)", value=-8.0, step=1.0)
    cooldown_sec = d2.number_input("Cooldown seconds", min_value=1, value=10, step=1)
    auto = d3.checkbox("Auto-run", value=False)
    dry = d4.checkbox("Dry-run (no enqueue)", value=False)

    e1, e2, e3, e4 = st.columns(4)
    target_roi_pct = e1.number_input("Target ROI % per trim", min_value=1.0, value=200.0, step=10.0)
    leverage = e2.number_input("Leverage for ROI math", min_value=1.0, value=500.0, step=1.0)
    trim_pct = e3.number_input("Trim % of winning leg", min_value=1.0, max_value=100.0, value=10.0, step=1.0)
    limit_offset_pct = e4.number_input("LIMIT offset %", min_value=0.0, value=0.05, step=0.01, format="%.4f")

    if autorefresh_available:
        r1, r2 = st.columns([1, 3])
        refresh_ms = r1.selectbox("Refresh interval", [2000, 3000, 5000, 8000], index=1)
        r2.caption("Auto-run uses autorefresh. If missing: pip install streamlit-autorefresh")
        if auto:
            st_autorefresh(interval=int(refresh_ms), key="strategy_autorefresh")
    else:
        st.warning("Auto-refresh not installed. For Auto-run: pip install streamlit-autorefresh. You can still use 'Run once'.")

    run_once = st.button("Run once (evaluate now)")

    if "strategy_last_action_ts" not in st.session_state:
        st.session_state.strategy_last_action_ts = 0.0
    if "strategy_log" not in st.session_state:
        st.session_state.strategy_log = []

    do_eval = run_once or auto

    if do_eval:
        acc = get_latest_account_snapshot(con)
        pos_ts, pos_rows = get_latest_positions_per_symbol_side(con)

        df_pos = pd.DataFrame(pos_rows) if pos_rows else pd.DataFrame(
            columns=["symbol", "side", "contracts", "entry_price", "unrealized_pnl"]
        )
        if not df_pos.empty:
            df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
            _to_float_series(df_pos, "contracts")
            _to_float_series(df_pos, "entry_price")
            _to_float_series(df_pos, "unrealized_pnl")

        L = get_leg(df_pos, sym, "LONG")
        S = get_leg(df_pos, sym, "SHORT")
        current_last_price = get_latest_ticker_price_for_symbol(con, sym)

        st.write("Latest snapshot:")
        st.json({"account_ts": (acc or {}).get("created_at"), "positions_ts": pos_ts})

        st.write("Legs:")
        st.json({"LONG": L, "SHORT": S})

        st.write("Market:")
        st.json({"last_price": current_last_price})

        st.write("Guards:")
        st.json({
            "active_queue_task": has_active_task_for_symbol(con, sym),
            "active_pending_limit": has_active_pending_limit_for_symbol(con, sym),
            "active_pending_chase": has_active_pending_chase_for_symbol(con, sym),
        })

        target_move_pct = float(target_roi_pct) / float(leverage) / 100.0

        st.write("Derived targets:")
        st.json({
            "target_roi_pct": float(target_roi_pct),
            "leverage": float(leverage),
            "target_move_pct": target_move_pct,
            "long_target_price": float(L["entry_price"] or 0.0) * (1.0 + target_move_pct) if float(L["contracts"] or 0.0) > 0 else None,
            "short_target_price": float(S["entry_price"] or 0.0) * (1.0 - target_move_pct) if float(S["contracts"] or 0.0) > 0 else None,
        })

        now = time.time()
        cooldown_left = max(0.0, float(cooldown_sec) - (now - float(st.session_state.strategy_last_action_ts)))

        decision = None

        if cooldown_left > 0:
            st.info(f"Cooldown active: {cooldown_left:.1f}s remaining.")
        elif has_active_task_for_symbol(con, sym):
            st.warning("Guard: ARMED/RUNNING task exists for this symbol. Waiting.")
        elif has_active_pending_limit_for_symbol(con, sym):
            st.warning("Guard: PENDING/TRIGGERED LIMIT follow-up exists for this symbol. Waiting.")
        elif has_active_pending_chase_for_symbol(con, sym):
            st.warning("Guard: PENDING CHASE follow-up exists for this symbol. Waiting.")
        else:
            # 1) No positions -> initial entry
            if L["contracts"] <= 0 and S["contracts"] <= 0:
                decision = ("OPEN", initial_side, base_qty, f"auto: initial {initial_side}")

            # 2) One-sided loss -> hedge open
            elif L["contracts"] > 0 and S["contracts"] <= 0:
                if L["unrealized_pnl"] <= float(hedge_loss_usdt):
                    decision = ("OPEN", "SHORT", base_qty, "auto: hedge SHORT")

            elif S["contracts"] > 0 and L["contracts"] <= 0:
                if S["unrealized_pnl"] <= float(hedge_loss_usdt):
                    decision = ("OPEN", "LONG", base_qty, "auto: hedge LONG")

            # 3) Hedged -> trim based on entry-based ROI target
            elif L["contracts"] > 0 and S["contracts"] > 0:
                long_entry = float(L["entry_price"] or 0.0)
                short_entry = float(S["entry_price"] or 0.0)
                long_upnl = float(L["unrealized_pnl"] or 0.0)
                short_upnl = float(S["unrealized_pnl"] or 0.0)

                long_target_price = long_entry * (1.0 + target_move_pct)
                short_target_price = short_entry * (1.0 - target_move_pct)

                long_ready = (
                    current_last_price is not None
                    and long_entry > 0
                    and current_last_price >= long_target_price
                )
                short_ready = (
                    current_last_price is not None
                    and short_entry > 0
                    and current_last_price <= short_target_price
                )

                if long_ready or short_ready:
                    if long_ready and short_ready:
                        if long_upnl >= short_upnl:
                            winner_side = "LONG"
                            winner_qty = float(L["contracts"] or 0.0)
                            winner_entry = long_entry
                        else:
                            winner_side = "SHORT"
                            winner_qty = float(S["contracts"] or 0.0)
                            winner_entry = short_entry
                    elif long_ready:
                        winner_side = "LONG"
                        winner_qty = float(L["contracts"] or 0.0)
                        winner_entry = long_entry
                    else:
                        winner_side = "SHORT"
                        winner_qty = float(S["contracts"] or 0.0)
                        winner_entry = short_entry

                    close_qty = max(1.0, round(winner_qty * (float(trim_pct) / 100.0)))
                    close_qty = float(min(close_qty, winner_qty))

                    price_ref = float(current_last_price) if current_last_price is not None else winner_entry

                    if winner_side == "LONG":
                        limit_price = price_ref * (1.0 + float(limit_offset_pct) / 100.0)
                    else:
                        limit_price = price_ref * (1.0 - float(limit_offset_pct) / 100.0)

                    decision = (
                        "CLOSE_LIMIT",
                        winner_side,
                        close_qty,
                        limit_price,
                        f"auto: trim {winner_side} winner by ROI target",
                    )
                else:
                    decision = None
            else:
                decision = None

        st.write("Decision:", decision or "—")

        if decision:
            if decision[0] == "OPEN":
                _, side, qty, note = decision

                if dry:
                    st.warning("DRY-RUN: would enqueue ARMED OPEN+CHASE.")
                else:
                    try:
                        enqueue_armed_open_chase(
                            con,
                            symbol=sym,
                            side=side,
                            qty=float(qty),
                            note=note,
                            priority=int(priority),
                        )
                        st.session_state.strategy_last_action_ts = time.time()
                        st.success("Enqueued ARMED action (OPEN+CHASE).")
                    except Exception as e:
                        st.error(f"Enqueue failed: {e}")

            elif decision[0] == "CLOSE_LIMIT":
                _, side, qty, limit_price, note = decision

                if dry:
                    st.warning(f"DRY-RUN: would enqueue ARMED CLOSE+LIMIT @ {limit_price:.6f}.")
                else:
                    try:
                        enqueue_armed_close_limit(
                            con,
                            symbol=sym,
                            side=side,
                            qty=float(qty),
                            limit_price=float(limit_price),
                            note=note,
                            priority=int(priority),
                        )
                        st.session_state.strategy_last_action_ts = time.time()
                        st.success(f"Enqueued ARMED action (CLOSE+LIMIT) @ {limit_price:.6f}.")
                    except Exception as e:
                        st.error(f"Enqueue failed: {e}")

            st.session_state.strategy_log.insert(
                0,
                {
                    "ts": now_utc_iso(),
                    "symbol": sym,
                    "decision": str(decision),
                    "dry": bool(dry),
                    "LONG": L,
                    "SHORT": S,
                    "last_price": current_last_price,
                },
            )
            st.session_state.strategy_log = st.session_state.strategy_log[:50]

    if st.session_state.get("strategy_log"):
        st.write("### Strategy log (last 50)")
        st.dataframe(pd.DataFrame(st.session_state.strategy_log), width="stretch")


# =================== Pending LIMIT ===================
with tab5:
    st.subheader("Pending LIMIT tasks")
    counts = get_pending_limit_status_counts(con)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("PENDING", counts.get("PENDING", 0))
    m2.metric("TRIGGERED", counts.get("TRIGGERED", 0))
    m3.metric("FILLED", counts.get("FILLED", 0))
    m4.metric("EXPIRED", counts.get("EXPIRED", 0))
    m5.metric("FAILED_AFTER_TRIGGER", counts.get("FAILED_AFTER_TRIGGER", 0))

    status_options = [
        "ALL",
        "PENDING",
        "TRIGGERED",
        "FILLED",
        "EXPIRED",
        "FAILED_AFTER_TRIGGER",
    ]

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        pending_status_filter = st.selectbox(
            "Status filter",
            status_options,
            index=0,
            key="pending_status_filter",
        )

    with col2:
        pending_limit_rows = list_pending_limit_tasks(con, status=pending_status_filter, limit=200)
        pending_ids = [int(r["id"]) for r in pending_limit_rows] if pending_limit_rows else []
        selected_task_id = st.selectbox(
            "Select task id",
            pending_ids if pending_ids else [0],
            key="selected_pending_limit_task_id",
        )

    with col3:
        if st.button("Refresh pending LIMIT tasks", key="refresh_pending_limit_tasks"):
            st.rerun()

    col4, col5, col6 = st.columns([1, 1, 1])

    with col4:
        if st.button("Reset selected task", key="reset_selected_pending_limit_task"):
            if selected_task_id:
                n = reset_pending_limit_task(con, int(selected_task_id))
                if n:
                    st.success(f"Reset task id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col5:
        if st.button("Set created_at=now", key="set_created_now_pending_limit_task"):
            if selected_task_id:
                n = set_pending_limit_created_now(con, int(selected_task_id))
                if n:
                    st.success(f"Updated created_at for task id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col6:
        if st.button("Delete resolved tasks", key="delete_resolved_pending_limit_tasks"):
            n = delete_resolved_pending_limit_tasks(con)
            st.success(f"Deleted resolved tasks: {n}")
            st.rerun()

    col7, col8, col9 = st.columns([1, 1, 1])

    with col7:
        if st.button("Mark selected FILLED", key="mark_selected_pending_limit_filled"):
            if selected_task_id:
                n = mark_pending_limit_task_status(
                    con,
                    int(selected_task_id),
                    "FILLED",
                    " | manual_mark_filled",
                )
                if n:
                    st.success(f"Marked FILLED: id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col8:
        if st.button("Mark selected FAILED", key="mark_selected_pending_limit_failed"):
            if selected_task_id:
                n = mark_pending_limit_task_status(
                    con,
                    int(selected_task_id),
                    "FAILED_AFTER_TRIGGER",
                    " | manual_mark_failed",
                )
                if n:
                    st.success(f"Marked FAILED_AFTER_TRIGGER: id={int(selected_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col9:
        if st.button("Delete selected task", key="delete_selected_pending_limit_task"):
            if selected_task_id:
                n = delete_pending_limit_task(con, int(selected_task_id))
                if n:
                    st.success(f"Deleted task id={int(selected_task_id)}")
                else:
                    st.warning("No task deleted")
                st.rerun()

    if pending_limit_rows:
        df_pending = pd.DataFrame([dict(r) for r in pending_limit_rows])

        display_cols = [
            "id",
            "action_id",
            "symbol",
            "side",
            "panel_mode",
            "limit_price",
            "qty",
            "status",
            "trigger_seen",
            "baseline_contracts",
            "triggered_at",
            "attempt_count",
            "created_at",
            "updated_at",
            "resolved_at",
            "note",
        ]
        display_cols = [c for c in display_cols if c in df_pending.columns]

        st.dataframe(
            df_pending[display_cols],
            width="stretch",
            hide_index=True,
        )

        if selected_task_id:
            selected_row = df_pending[df_pending["id"] == selected_task_id]
            if not selected_row.empty:
                st.markdown("**Selected task details**")
                st.json(selected_row.iloc[0].to_dict())
    else:
        st.info("No pending_limit_tasks rows for the selected filter.")


# =================== Pending CHASE ===================
with tab6:
    st.subheader("Pending CHASE tasks")
    counts = get_pending_chase_status_counts(con)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("PENDING", counts.get("PENDING", 0))
    m2.metric("FILLED", counts.get("FILLED", 0))
    m3.metric("EXPIRED", counts.get("EXPIRED", 0))
    m4.metric("FAILED", counts.get("FAILED", 0))

    status_options = [
        "ALL",
        "PENDING",
        "FILLED",
        "EXPIRED",
        "FAILED",
    ]

    col1, col2, col3 = st.columns([1, 1, 1])

    with col1:
        pending_chase_status_filter = st.selectbox(
            "Status filter",
            status_options,
            index=0,
            key="pending_chase_status_filter",
        )

    with col2:
        pending_chase_rows = list_pending_chase_tasks(con, status=pending_chase_status_filter, limit=200)
        pending_chase_ids = [int(r["id"]) for r in pending_chase_rows] if pending_chase_rows else []
        selected_chase_task_id = st.selectbox(
            "Select task id",
            pending_chase_ids if pending_chase_ids else [0],
            key="selected_pending_chase_task_id",
        )

    with col3:
        if st.button("Refresh pending CHASE tasks", key="refresh_pending_chase_tasks"):
            st.rerun()

    col4, col5, col6 = st.columns([1, 1, 1])

    with col4:
        if st.button("Reset selected CHASE", key="reset_selected_pending_chase_task"):
            if selected_chase_task_id:
                n = reset_pending_chase_task(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Reset CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col5:
        if st.button("Set CHASE created_at=now", key="set_created_now_pending_chase_task"):
            if selected_chase_task_id:
                n = set_pending_chase_created_now(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Updated created_at for CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col6:
        if st.button("Delete resolved CHASE", key="delete_resolved_pending_chase_tasks"):
            n = delete_resolved_pending_chase_tasks(con)
            st.success(f"Deleted resolved CHASE tasks: {n}")
            st.rerun()

    col7, col8, col9 = st.columns([1, 1, 1])

    with col7:
        if st.button("Mark selected CHASE FILLED", key="mark_selected_pending_chase_filled"):
            if selected_chase_task_id:
                n = mark_pending_chase_task_status(
                    con,
                    int(selected_chase_task_id),
                    "FILLED",
                    " | manual_mark_filled",
                )
                if n:
                    st.success(f"Marked CHASE FILLED: id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col8:
        if st.button("Mark selected CHASE FAILED", key="mark_selected_pending_chase_failed"):
            if selected_chase_task_id:
                n = mark_pending_chase_task_status(
                    con,
                    int(selected_chase_task_id),
                    "FAILED",
                    " | manual_mark_failed",
                )
                if n:
                    st.success(f"Marked CHASE FAILED: id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task updated")
                st.rerun()

    with col9:
        if st.button("Delete selected CHASE", key="delete_selected_pending_chase_task"):
            if selected_chase_task_id:
                n = delete_pending_chase_task(con, int(selected_chase_task_id))
                if n:
                    st.success(f"Deleted CHASE task id={int(selected_chase_task_id)}")
                else:
                    st.warning("No task deleted")
                st.rerun()

    if pending_chase_rows:
        df_pending_chase = pd.DataFrame([dict(r) for r in pending_chase_rows])

        display_cols = [
            "id",
            "action_id",
            "symbol",
            "side",
            "panel_mode",
            "qty",
            "status",
            "baseline_contracts",
            "filled_contracts",
            "created_at",
            "updated_at",
            "resolved_at",
            "note",
        ]
        display_cols = [c for c in display_cols if c in df_pending_chase.columns]

        st.dataframe(
            df_pending_chase[display_cols],
            width="stretch",
            hide_index=True,
        )

        if selected_chase_task_id:
            selected_row = df_pending_chase[df_pending_chase["id"] == selected_chase_task_id]
            if not selected_row.empty:
                st.markdown("**Selected CHASE task details**")
                st.json(selected_row.iloc[0].to_dict())
    else:
        st.info("No pending_chase_tasks rows for the selected filter.")

con.close()
# app/streamlit_app.py
# Copy–paste ready. Includes:
# - Dashboard (latest account snapshot + latest positions per symbol+side)
# - Aggregates with risk_flag + suggested_next_action
# - Action Queue (creates PENDING actions; ARM/CANCEL; shows list)
# - Action Ledger (manual notes)
#
# Run:
#   python -m streamlit run app/streamlit_app.py

import sys
from pathlib import Path
from datetime import datetime, timezone
import hashlib

import pandas as pd
import streamlit as st
import uuid

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

    # One-sided position => always high risk in our framework
    if net == gross:
        return "🔴 HIGH RISK – REDUCE ONLY"

    hedge_ratio = 1 - (net / gross)  # 0..1

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

    # One-sided
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
    """
    Returns last row per (symbol, side) by max(id).
    """
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


# ---------- Action Queue (v2) helpers ----------

def make_idempotency_key_v2(p: dict) -> str:
    # HARD guarantee uniqueness per click
    nonce = uuid.uuid4().hex
    s = (
        f"{nonce}|"
        f"{p.get('symbol')}|{p.get('panel_mode')}|{p.get('side')}|{p.get('order_kind')}|"
        f"qty={p.get('qty')}|price={p.get('limit_price')}|"
        f"reduce={p.get('reduce_only',1)}|prio={p.get('priority',100)}"
    )
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def queue_insert_v2(con, p: dict) -> int:
    p = dict(p)

    # Always generate a unique idempotency_key per click (command queue semantics).
    # This avoids "Ignored duplicate" and lets you queue repeated actions intentionally.
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
            p.get("intent", "close"),          # keep your existing schema semantics
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


# ---------- UI ----------
st.set_page_config(page_title="MEXC Monitor", layout="wide")
st.title("MEXC Monitor (read-only)")

con = connect()

tab1, tab2, tab3 = st.tabs(["Dashboard", "Action Queue", "Action Ledger"])


# =================== Dashboard ===================
with tab1:
    st.subheader("Live data (collector snapshots)")

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

        # Net exposure: LONG contracts - SHORT contracts
        df_pos["side"] = df_pos["side"].fillna("").astype(str).str.upper()
        df_pos["signed_contracts"] = df_pos.apply(
            lambda r: r["contracts"] if r["side"] == "LONG" else (-r["contracts"] if r["side"] == "SHORT" else 0.0),
            axis=1,
        )

        df_agg = (
            df_pos.groupby("symbol", as_index=False)
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
            st.dataframe(df_pos, width="stretch")

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

    st.caption("Tip: For Post-Only closing, use Panel mode=CLOSE, Order kind=POST_ONLY.")

    with st.form("queue_create_v2", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        symbol = c1.text_input("Symbol", value="AT/USDT")
        panel_mode = c2.selectbox("Panel mode", ["CLOSE", "OPEN"], index=0)
        side = c3.selectbox("Side", ["SHORT", "LONG"], index=0)
        qty = c4.number_input("Qty (Cont)", min_value=0.0, value=50.0, step=1.0)

        c5, c6, c7, c8 = st.columns(4)
        order_kind = c5.selectbox(
            "Order kind",
            ["POST_ONLY", "LIMIT", "MARKET", "CHASE_LIMIT", "TRIGGER", "TRAILING_STOP"],
            index=0,
        )
        limit_price = c6.number_input(
            "Limit price (USDT) (0 = auto)",
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

            # For kinds that require a price, allow 0=auto (Tampermonkey will try)
            # If you want strict enforcement, uncomment this:
            # if order_kind in ("POST_ONLY", "LIMIT", "CHASE_LIMIT") and limit_price <= 0:
            #     st.error("For POST_ONLY/LIMIT/CHASE_LIMIT set Limit price > 0 (or keep 0 for auto only if TM supports it).")
            #     st.stop()

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

                # keep order_type for compatibility with your schema
                "order_type": "limit" if order_kind in ("POST_ONLY", "LIMIT", "CHASE_LIMIT") else "market",
                "limit_price": float(limit_price) if limit_price > 0 else None,

                "trigger_type": trigger_type,
                "trigger_op": trigger_op if trigger_type == "price" else None,
                "trigger_price": float(trigger_price) if trigger_type == "price" else None,

                "status": "PENDING",
                "priority": int(priority),

                "note": note.strip() if note.strip() else None,

                # new UI control fields you added
                "panel_mode": panel_mode,
                "order_kind": order_kind,
                "leverage": None,
            }

            inserted = queue_insert_v2(con, p)
            con.commit()
            if inserted == 1:
                st.success("Created PENDING action.")
            else:
                st.error("Insert failed (unexpected). Check logs.")

    st.divider()

    f1, f2 = st.columns(2)
    status_filter = f1.selectbox(
        "Status filter",
        ["ALL", "PENDING", "ARMED", "RUNNING", "DONE", "FAILED", "CANCELED"],
        index=0,
    )
    limit = f2.number_input("Rows", min_value=20, value=200, step=20)

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
        if cC.button("CANCEL selected"):
            queue_cancel(con, int(action_id))
            con.commit()
            st.success(f"CANCELED {action_id}")
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

    actions = get_recent_actions(con, limit=50)
    if actions:
        df_actions = pd.DataFrame(actions)
        st.dataframe(df_actions, width="stretch")
    else:
        st.caption("No actions yet.")

con.close()

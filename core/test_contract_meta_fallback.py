from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import core.mexc_direct as mexc_direct
from core.streamlit_services.contract_rules_service import normalize_order_inputs


def main() -> None:
    db_path = ROOT_DIR / "data" / "_test_contract_meta_fallback.sqlite"
    try:
        if db_path.exists():
            db_path.unlink()

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                """
                CREATE TABLE symbols_state (
                    symbol TEXT,
                    exchange_symbol TEXT,
                    price_tick REAL,
                    qty_step REAL,
                    min_qty REAL,
                    price_precision INTEGER,
                    qty_precision INTEGER
                )
                """
            )
            conn.execute(
                """
                INSERT INTO symbols_state (
                    symbol, exchange_symbol, price_tick, qty_step, min_qty, price_precision, qty_precision
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("ADAUSDT", "ADA/USDT:USDT", 0.0001, 1.0, 1.0, 4, 0),
            )
            conn.commit()
        finally:
            conn.close()

        old_db_path = mexc_direct.DB_PATH
        old_get_contract_detail_raw = mexc_direct.get_contract_detail_raw
        try:
            mexc_direct.DB_PATH = db_path

            def _raise_no_live_meta(symbol=None):
                raise RuntimeError("no live contract meta in test")

            mexc_direct.get_contract_detail_raw = _raise_no_live_meta
            mexc_direct.clear_public_market_cache()

            meta = mexc_direct.get_contract_meta("ADA/USDT:USDT")
            assert meta is not None, meta
            assert meta["price_tick"] == 0.0001, meta
            assert meta["contract_meta_source"] == "symbols_state", meta

            normalized = normalize_order_inputs(
                symbol="ADA/USDT:USDT",
                side="SHORT",
                qty=1.0,
                limit_price=0.2423211,
                require_limit_price=True,
            )
            assert normalized["limit_price"] == 0.2424, normalized

            print("OK: contract meta falls back to symbols_state and snaps price by tick.")
        finally:
            mexc_direct.DB_PATH = old_db_path
            mexc_direct.get_contract_detail_raw = old_get_contract_detail_raw
            mexc_direct.clear_public_market_cache()
    finally:
        try:
            if db_path.exists():
                db_path.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()

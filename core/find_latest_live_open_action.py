from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.reprice_worker import list_reprice_candidates, RepriceWorkerConfig
from core.reprice_service import (
    RepriceServiceConfig,
    classify_overshoot_kind,
    get_open_order_item,
    normalize_api_order_id,
    safe_float,
)


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


def parse_created_at(value: Any) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    s = str(value).strip()
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def build_test_prices(
    *,
    panel_mode: str,
    side: str,
    current_price: float,
) -> Dict[str, float]:
    """
    Малки тестови отмествания:
    - около 5 bps favorable
    - около 20 bps adverse
    За да имаме добър шанс:
    - favorable да удари would_replace при favorable_threshold=1
    - adverse да удари would_replace при adverse_threshold=10
    """
    favorable_bps = 5.0
    adverse_bps = 20.0

    favorable_ratio = favorable_bps / 10000.0
    adverse_ratio = adverse_bps / 10000.0

    pm = str(panel_mode).upper().strip()
    sd = str(side).upper().strip()

    if pm == "OPEN" and sd == "SHORT":
        favorable_price = current_price * (1.0 - favorable_ratio)
        adverse_price = current_price * (1.0 + adverse_ratio)
    elif pm == "OPEN" and sd == "LONG":
        favorable_price = current_price * (1.0 + favorable_ratio)
        adverse_price = current_price * (1.0 - adverse_ratio)
    elif pm == "CLOSE" and sd == "LONG":
        favorable_price = current_price * (1.0 + favorable_ratio)
        adverse_price = current_price * (1.0 - adverse_ratio)
    elif pm == "CLOSE" and sd == "SHORT":
        favorable_price = current_price * (1.0 - favorable_ratio)
        adverse_price = current_price * (1.0 + adverse_ratio)
    else:
        favorable_price = current_price
        adverse_price = current_price

    return {
        "same_price": round(current_price, 10),
        "favorable_test_price": round(favorable_price, 10),
        "adverse_test_price": round(adverse_price, 10),
    }


def main() -> None:
    cfg = RepriceWorkerConfig(
        service=RepriceServiceConfig(
            threshold_bps=2.0,
            favorable_threshold_bps=1.0,
            adverse_threshold_bps=10.0,
            max_order_age_sec=None,
            require_order_open=True,
            update_action_queue=True,
        ),
        statuses=("DONE",),
        order_kinds=("LIMIT", "POST_ONLY"),
        max_items=200,
        poll_interval_sec=2.0,
    )

    candidates = list_reprice_candidates(config=cfg)

    live_items: List[Dict[str, Any]] = []
    for row in candidates:
        symbol = str(row.get("symbol"))
        api_order_id = normalize_api_order_id(row.get("api_order_id"))
        if not api_order_id:
            continue

        open_item = get_open_order_item(symbol, api_order_id)
        if open_item is None:
            continue

        open_price = safe_float(open_item.get("price"))
        if open_price is None:
            open_price = safe_float(open_item.get("priceStr"))
        if open_price is None:
            open_price = safe_float(row.get("limit_price"))

        if open_price is None:
            continue

        panel_mode = str(row.get("panel_mode") or "")
        side = str(row.get("side") or "")

        test_prices = build_test_prices(
            panel_mode=panel_mode,
            side=side,
            current_price=float(open_price),
        )

        live_items.append(
            {
                "id": int(row["id"]),
                "created_at": row.get("created_at"),
                "symbol": symbol,
                "status": row.get("status"),
                "order_kind": row.get("order_kind"),
                "panel_mode": panel_mode,
                "side": side,
                "qty": row.get("qty"),
                "limit_price": row.get("limit_price"),
                "api_order_id": api_order_id,
                "created_by": row.get("created_by"),
                "open_price": open_price,
                "test_prices": test_prices,
                "favorable_kind": classify_overshoot_kind(
                    panel_mode=panel_mode,
                    side=side,
                    current_price=float(open_price),
                    target_price=float(test_prices["favorable_test_price"]),
                ),
                "adverse_kind": classify_overshoot_kind(
                    panel_mode=panel_mode,
                    side=side,
                    current_price=float(open_price),
                    target_price=float(test_prices["adverse_test_price"]),
                ),
            }
        )

    live_items.sort(key=lambda x: parse_created_at(x.get("created_at")), reverse=True)

    out = {
        "count": len(live_items),
        "latest": live_items[0] if live_items else None,
        "items": live_items[:10],
    }
    print(safe_json(out))


if __name__ == "__main__":
    main()
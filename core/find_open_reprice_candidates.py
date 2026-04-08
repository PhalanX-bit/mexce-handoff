from __future__ import annotations

import json
from typing import Any, Dict, List

from core.reprice_worker import list_reprice_candidates, RepriceWorkerConfig
from core.reprice_service import RepriceServiceConfig, get_open_order_item


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(value)


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
        max_items=100,
        poll_interval_sec=2.0,
    )

    rows = list_reprice_candidates(config=cfg)

    found: List[Dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol"))
        api_order_id = row.get("api_order_id")
        open_item = get_open_order_item(symbol, api_order_id)

        if open_item is not None:
            found.append(
                {
                    "id": row.get("id"),
                    "symbol": symbol,
                    "status": row.get("status"),
                    "order_kind": row.get("order_kind"),
                    "panel_mode": row.get("panel_mode"),
                    "side": row.get("side"),
                    "qty": row.get("qty"),
                    "limit_price": row.get("limit_price"),
                    "api_order_id": row.get("api_order_id"),
                    "created_by": row.get("created_by"),
                    "open_price": open_item.get("price") or open_item.get("priceStr"),
                    "open_item": open_item,
                }
            )

    print(safe_json({"count": len(found), "items": found}))


if __name__ == "__main__":
    main()
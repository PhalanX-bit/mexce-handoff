from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_URL = "https://api.mexc.com"


def short(obj: Any, limit: int = 3000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        s = repr(obj)
    if len(s) <= limit:
        return s
    return s[:limit] + "\n... [truncated]"


def load_keys() -> tuple[str, str]:
    load_dotenv()
    api_key = os.getenv("MEXC_API_KEY", "").strip()
    api_secret = os.getenv("MEXC_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("Missing MEXC_API_KEY / MEXC_API_SECRET in .env")
    return api_key, api_secret


def sign_v1(access_key: str, secret_key: str, req_time: str, param_str: str) -> str:
    # MEXC Futures OPEN-API signature:
    # accessKey + timestamp + parameterString
    payload = access_key + req_time + param_str
    return hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def private_post(
    path: str,
    payload: Dict[str, Any] | list[Any],
    api_key: str,
    api_secret: str,
    timeout: int = 20,
) -> requests.Response:
    body_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    req_time = str(int(time.time() * 1000))
    signature = sign_v1(api_key, api_secret, req_time, body_str)

    headers = {
        "Content-Type": "application/json",
        "ApiKey": api_key,
        "Request-Time": req_time,
        "Signature": signature,
        "Recv-Window": "10000",
    }

    return requests.post(
        BASE_URL + path,
        data=body_str.encode("utf-8"),
        headers=headers,
        timeout=timeout,
    )


def private_get(
    path: str,
    api_key: str,
    api_secret: str,
    timeout: int = 20,
) -> requests.Response:
    req_time = str(int(time.time() * 1000))
    param_str = ""
    signature = sign_v1(api_key, api_secret, req_time, param_str)

    headers = {
        "ApiKey": api_key,
        "Request-Time": req_time,
        "Signature": signature,
        "Recv-Window": "10000",
    }

    return requests.get(
        BASE_URL + path,
        headers=headers,
        timeout=timeout,
    )


def try_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


def main() -> None:
    api_key, api_secret = load_keys()

    # Безопасен далечен futures LIMIT sell
    symbol = "BTC_USDT"
    price = 80500.0
    vol = 1  # 1 contract

    # По твоите вече върнати futures orders:
    # category=1 -> limit
    # side=1 open long, 2 close short, 3 open short, 4 close long
    side = 3         # open short
    category = 1     # limit
    open_type = 2    # cross
    leverage = 500

    create_payload = {
        "symbol": symbol,
        "price": price,
        "vol": vol,
        "side": side,
        "type": open_type,
        "openType": open_type,
        "leverage": leverage,
        "category": category,
        "externalOid": f"direct_test_{int(time.time())}",
    }

    print("=== DIRECT CREATE TEST PAYLOAD ===")
    print(short(create_payload))

    print("\n=== POST create order ===")
    create_paths = [
        "/api/v1/private/order/submit",
        "/api/v1/private/order/place",
        "/api/v1/private/order/create",
    ]

    created_json: Optional[Dict[str, Any]] = None
    used_create_path: Optional[str] = None

    for path in create_paths:
        try:
            resp = private_post(path, create_payload, api_key, api_secret)
            data = try_json(resp)

            print(f"\nPATH {path}")
            print("HTTP", resp.status_code)
            print(short(data, 2500))

            if isinstance(data, dict) and data.get("success") is True:
                created_json = data
                used_create_path = path
                break
        except Exception as e:
            print(f"\nPATH {path}")
            print(type(e).__name__, str(e))

    if not created_json:
        print("\nRESULT: create did not succeed on tested direct paths.")
        return

    print("\n=== CREATE SUCCESS ===")
    print("used path:", used_create_path)
    print(short(created_json, 2500))

    order_id = None
    data_field = created_json.get("data")
    if isinstance(data_field, str):
        order_id = data_field
    elif isinstance(data_field, int):
        order_id = str(data_field)
    elif isinstance(data_field, dict):
        order_id = str(data_field.get("orderId") or data_field.get("id") or "")

    print("order_id:", order_id)

    time.sleep(2)

    print("\n=== GET open orders raw ===")
    open_paths = [
        f"/api/v1/private/order/list/open_orders/{symbol}",
        f"/api/v1/private/order/list/open_orders?symbol={symbol}",
    ]

    open_ok = False
    for path in open_paths:
        try:
            resp = private_get(path, api_key, api_secret)
            data = try_json(resp)
            print(f"\nPATH {path}")
            print("HTTP", resp.status_code)
            print(short(data, 3500))
            if resp.status_code == 200:
                open_ok = True
        except Exception as e:
            print(f"\nPATH {path}")
            print(type(e).__name__, str(e))

    if order_id:
        print("\n=== POST cancel order ===")
        cancel_payload = [int(order_id)]
        cancel_resp = private_post(
            "/api/v1/private/order/cancel",
            cancel_payload,
            api_key,
            api_secret,
        )
        cancel_data = try_json(cancel_resp)
        print("HTTP", cancel_resp.status_code)
        print(short(cancel_data, 2500))
    else:
        print("\nSKIP cancel: no order_id extracted.")

    if open_ok:
        time.sleep(2)
        print("\n=== GET open orders raw after cancel ===")
        for path in open_paths:
            try:
                resp = private_get(path, api_key, api_secret)
                data = try_json(resp)
                print(f"\nPATH {path}")
                print("HTTP", resp.status_code)
                print(short(data, 3500))
            except Exception as e:
                print(f"\nPATH {path}")
                print(type(e).__name__, str(e))


if __name__ == "__main__":
    main()
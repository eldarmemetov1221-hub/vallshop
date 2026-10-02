"""Диагностика подписи BoltUtil: перебирает варианты схемы HMAC и показывает,
какой принимает сервер. Бьёт по /order/status (реальные заказы НЕ создаются).

Запуск в контейнере:
    docker compose exec bot python -m scripts.bolt_probe
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import httpx

BASE = os.getenv("BOLT_BASE_URL", "https://api.boltutil.com").rstrip("/")
KEY = os.getenv("BOLT_API_KEY", "")
SECRET = os.getenv("BOLT_SECRET", "")
PATH = "/api/v1/order/status"

payload = {"externalOrderId": "probe-signature-test"}
body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
ts_ms = str(int(time.time() * 1000))
ts_s = str(int(time.time()))


def hx(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def b64(secret: str, msg: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()


SCHEMES = [
    ("A ts_ms '.' body hex", ts_ms, hx(SECRET, f"{ts_ms}.{body}")),
    ("B body only hex", ts_ms, hx(SECRET, body)),
    ("C ts_ms+body (no dot) hex", ts_ms, hx(SECRET, f"{ts_ms}{body}")),
    ("D ts_ms '.' body base64", ts_ms, b64(SECRET, f"{ts_ms}.{body}")),
    ("E ts_sec '.' body hex", ts_s, hx(SECRET, f"{ts_s}.{body}")),
    ("F body '.' ts_ms hex", ts_ms, hx(SECRET, f"{body}.{ts_ms}")),
    ("G ts_ms '.' body hex UPPER", ts_ms, hx(SECRET, f"{ts_ms}.{body}").upper()),
]


def main() -> None:
    print(f"BASE={BASE} KEY_len={len(KEY)} SECRET_len={len(SECRET)}")
    print(f"body={body}")
    with httpx.Client(timeout=20) as client:
        for name, ts, sig in SCHEMES:
            headers = {
                "Content-Type": "application/json",
                "X-Bolt-Key": KEY,
                "X-Bolt-Timestamp": ts,
                "X-Bolt-Signature": sig,
            }
            try:
                r = client.post(BASE + PATH, content=body.encode(), headers=headers)
                msg = r.text[:160].replace("\n", " ")
                print(f"[{name}] -> HTTP {r.status_code}: {msg}")
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] -> ERROR {exc}")


if __name__ == "__main__":
    main()

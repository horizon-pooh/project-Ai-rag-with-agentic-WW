"""tests/api/_app.py — สร้าง app ของจริง (create_app) แต่ใส่ app.state เป็น fake โดยไม่รัน lifespan"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi import FastAPI

from app.main import create_app
from tests.fakes import make_deps, make_settings
from tests.infra_fakes import make_queue

SECRET = "test-channel-secret"


def make_test_app(**deps_overrides: Any) -> tuple[FastAPI, Any]:
    settings = make_settings(line_channel_secret=SECRET)
    deps = make_deps(settings=settings, **deps_overrides)
    app = create_app(settings)
    app.state.deps = deps
    # คิว procrastinate บน InMemoryConnector: งานที่ใส่ดูได้ที่ app.state.queue.connector.jobs
    app.state.queue, _ = make_queue()
    app.state.pipeline_cfg = object()
    return app, deps


def sign(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def body_of(*events: dict[str, Any]) -> bytes:
    return json.dumps({"destination": "Uxxx", "events": list(events)}).encode()


def text_event(
    n: int = 1, text: str = "หาตู้เย็น", user: str = "U1", token: str | None = "rt-1"
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "type": "message",
        "webhookEventId": f"wh-{n}",
        "timestamp": 1_792_000_000_000 + n,
        "source": {"type": "user", "userId": user},
        "message": {"type": "text", "id": f"m-{n}", "text": text},
        "deliveryContext": {"isRedelivery": False},
    }
    if token is not None:
        ev["replyToken"] = token
    return ev


def postback_event(n: int, data: str, user: str = "U1") -> dict[str, Any]:
    return {
        "type": "postback",
        "webhookEventId": f"wh-{n}",
        "timestamp": 1_792_000_000_000 + n,
        "source": {"type": "user", "userId": user},
        "postback": {"data": data},
        "replyToken": f"rt-{n}",
    }

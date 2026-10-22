"""tests/api/test_webhook.py — POST /webhook (fake ทั้งหมด ไม่รัน lifespan)

ข้อตัดสิน: webhook_event_id ซ้ำ -> ไม่เก็บ replyToken ไม่ใส่คิวซ้ำ ไม่วิ่งทางด่วน ตอบ 200
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.pipeline import orchestrator
from app.pipeline.schemas import InboundEvent, ProcessStatus
from tests.api._app import body_of, make_test_app, postback_event, sign, text_event
from tests.infra_fakes import FakeArqPool, FakeTurns


@pytest.fixture
def turns(monkeypatch: pytest.MonkeyPatch) -> FakeTurns:
    fake = FakeTurns()
    monkeypatch.setattr(orchestrator, "run_turn", fake, raising=False)
    return fake


@pytest.fixture
def fast_seen(monkeypatch: pytest.MonkeyPatch) -> list[InboundEvent]:
    """is_fast_path_event ปลอม: เฉพาะ postback action=talk_to_staff"""
    seen: list[InboundEvent] = []

    def is_fast(ev: InboundEvent, cfg: Any) -> bool:
        seen.append(ev)
        return ev.postback_action == "talk_to_staff"

    monkeypatch.setattr(orchestrator, "is_fast_path_event", is_fast, raising=False)
    return seen


def _post(client: TestClient, body: bytes, signature: str | None) -> Any:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Line-Signature"] = signature
    return client.post("/webhook", content=body, headers=headers)


def test_missing_signature_401(fast_seen: Any) -> None:
    app, deps = make_test_app()
    resp = _post(TestClient(app), body_of(text_event()), None)
    assert resp.status_code == 401
    assert deps.raw_events.db.raw_events == {}
    assert app.state.arq.calls == []


def test_bad_signature_401(fast_seen: Any) -> None:
    app, deps = make_test_app()
    body = body_of(text_event())
    resp = _post(TestClient(app), body, sign(body, "wrong-secret"))
    assert resp.status_code == 401
    assert deps.raw_events.db.raw_events == {}


def test_signature_is_over_raw_body(fast_seen: Any) -> None:
    app, deps = make_test_app()
    body = body_of(text_event())
    # body ต่างไปหนึ่งไบต์ -> signature เดิมใช้ไม่ได้
    resp = _post(TestClient(app), body + b" ", sign(body))
    assert resp.status_code == 401
    assert deps.raw_events.db.raw_events == {}


async def test_valid_event_saved_token_stored_and_enqueued(
    fast_seen: Any, turns: FakeTurns
) -> None:
    app, deps = make_test_app()
    body = body_of(text_event(1, token="rt-secret"))
    resp = _post(TestClient(app), body, sign(body))
    assert resp.status_code == 200
    rows = list(deps.raw_events.db.raw_events.values())
    assert len(rows) == 1
    raw = rows[0]
    assert raw.webhook_event_id == "wh-1"
    assert raw.line_user_id == "U1"
    assert raw.event_type == "message"
    assert "replyToken" not in raw.payload
    assert "rt-secret" not in str(raw.payload)
    assert raw.process_status is ProcessStatus.PENDING
    assert await deps.reply_tokens.take(raw.id) == "rt-secret"
    assert app.state.arq.calls == [("process_event", (raw.id,), f"raw:{raw.id}")]
    # ข้อความธรรมดาไม่วิ่งทางด่วน
    assert turns.calls == []


def test_duplicate_webhook_event_not_reenqueued(fast_seen: Any) -> None:
    app, deps = make_test_app()
    client = TestClient(app)
    body = body_of(text_event(1))
    assert _post(client, body, sign(body)).status_code == 200
    app.state.arq.finish("raw:1")  # งานแรกจบแล้ว: ถ้าใส่ซ้ำ arq จะรับ -> webhook ต้องไม่ใส่เอง
    assert _post(client, body, sign(body)).status_code == 200
    assert len(deps.raw_events.db.raw_events) == 1
    assert len(app.state.arq.calls) == 1


def test_fast_path_event_scheduled_and_also_enqueued(
    fast_seen: list[InboundEvent], turns: FakeTurns
) -> None:
    app, deps = make_test_app()
    body = body_of(postback_event(5, "action=talk_to_staff"))
    resp = _post(TestClient(app), body, sign(body))
    assert resp.status_code == 200
    raw_id = next(iter(deps.raw_events.db.raw_events))
    # BackgroundTask รันแล้ว (TestClient รอให้จบ): claim -> run_turn -> done
    assert turns.calls == [raw_id]
    assert deps.raw_events.db.raw_events[raw_id].process_status is ProcessStatus.DONE
    assert fast_seen[0].postback_action == "talk_to_staff"
    assert app.state.arq.calls == [("process_event", (raw_id,), f"raw:{raw_id}")]


def test_fast_path_skips_when_already_claimed(
    fast_seen: list[InboundEvent], turns: FakeTurns, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, deps = make_test_app()
    orig_enqueue = app.state.arq.enqueue_job

    async def enqueue_and_claim(fn: str, raw_id: int, **kw: Any) -> Any:
        # จำลอง worker หยิบงานไป claim ก่อน BackgroundTask ของทางด่วนจะเริ่ม
        await deps.raw_events.claim(raw_id)
        return await orig_enqueue(fn, raw_id, **kw)

    monkeypatch.setattr(app.state.arq, "enqueue_job", enqueue_and_claim)
    body = body_of(postback_event(6, "action=talk_to_staff"))
    assert _post(TestClient(app), body, sign(body)).status_code == 200
    assert turns.calls == []


def test_enqueue_failure_still_200(fast_seen: Any) -> None:
    app, deps = make_test_app()
    app.state.arq = FakeArqPool(fail=True)
    body = body_of(text_event(1))
    resp = _post(TestClient(app), body, sign(body))
    assert resp.status_code == 200
    raw = next(iter(deps.raw_events.db.raw_events.values()))
    assert raw.process_status is ProcessStatus.PENDING


def test_bad_json_400(fast_seen: Any) -> None:
    app, _ = make_test_app()
    body = b"{not json"
    assert _post(TestClient(app), body, sign(body)).status_code == 400


def test_empty_events_200(fast_seen: Any) -> None:
    # ปุ่ม Verify ของ LINE ส่ง events ว่าง
    app, deps = make_test_app()
    body = body_of()
    assert _post(TestClient(app), body, sign(body)).status_code == 200
    assert deps.raw_events.db.raw_events == {}


def test_event_without_user_saved_without_fast_path(fast_seen: list[Any]) -> None:
    app, deps = make_test_app()
    ev = postback_event(9, "action=talk_to_staff")
    ev["source"] = {"type": "group", "groupId": "G1"}
    body = body_of(ev)
    assert _post(TestClient(app), body, sign(body)).status_code == 200
    raw = next(iter(deps.raw_events.db.raw_events.values()))
    assert raw.line_user_id is None
    assert fast_seen == []
    assert len(app.state.arq.calls) == 1


def test_event_without_webhook_event_id_skipped(fast_seen: Any) -> None:
    app, deps = make_test_app()
    ev = text_event(1)
    del ev["webhookEventId"]
    body = body_of(ev, text_event(2))
    assert _post(TestClient(app), body, sign(body)).status_code == 200
    assert [r.webhook_event_id for r in deps.raw_events.db.raw_events.values()] == ["wh-2"]

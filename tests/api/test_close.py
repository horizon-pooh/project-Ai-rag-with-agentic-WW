"""tests/api/test_close.py — GET /close?token= (หน้ายืนยัน ไม่มีผลข้างเคียง) และ POST /close (ปิดจริง)"""

from __future__ import annotations

import html
from typing import Any

from fastapi.testclient import TestClient

from app.api.close import MSG_ALREADY_BOT, MSG_CLOSED, MSG_CONFIRM, MSG_INVALID
from app.pipeline import replies
from app.pipeline.schemas import ConversationStatus, HandoffEndReason
from tests.api._app import make_test_app


async def _human_case(app: Any, deps: Any, line_user_id: str = "U1") -> tuple[Any, str]:
    user = await deps.users.get_or_create(line_user_id)
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    conv = await deps.conversations.start_handoff(conv.id, "test")
    token = await deps.conversations.create_close_token(
        conv.id, conv.handoff_started_at, deps.settings.close_token_ttl
    )
    return conv, token


def _post(client: TestClient, token: str | None) -> Any:
    data = {} if token is None else {"token": token}
    return client.post("/close", data=data)


def _assert_hardened(resp: Any) -> None:
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in resp.headers["x-robots-tag"]
    assert resp.headers["content-type"].startswith("text/html")


async def test_get_is_side_effect_free_then_post_closes() -> None:
    app, deps = make_test_app()
    conv, token = await _human_case(app, deps)
    client = TestClient(app)

    # bot แสดงตัวอย่างลิงก์เปิด GET กี่ครั้งก็ไม่ปิดเคส ไม่เผา token
    for _ in range(2):
        page = client.get("/close", params={"token": token})
        assert page.status_code == 200
        assert MSG_CONFIRM in page.text
        assert 'method="post"' in page.text
        assert f'name="token" value="{token}"' in page.text
        _assert_hardened(page)
    assert (await deps.conversations.get(conv.id)).status is ConversationStatus.HUMAN
    assert deps.line.pushes == []
    assert all(row.used_at is None for row in deps.conversations.db.close_tokens.values())

    resp = _post(client, token)
    assert resp.status_code == 200
    assert MSG_CLOSED in resp.text
    assert token not in resp.text
    _assert_hardened(resp)
    now_conv = await deps.conversations.get(conv.id)
    assert now_conv.status is ConversationStatus.BOT
    assert now_conv.handoff_end_reason is HandoffEndReason.STAFF_CLOSED
    assert [m for _, m, _ in deps.line.pushes] == [[{"type": "text", "text": replies.RESUMED}]]
    assert deps.line.pushes[0][0] == "U1"
    assert len(deps.notifier.ended) == 1


async def test_post_used_token_invalid_and_no_second_push() -> None:
    app, deps = make_test_app()
    _, token = await _human_case(app, deps)
    client = TestClient(app)
    assert MSG_CLOSED in _post(client, token).text
    again = _post(client, token)
    assert again.status_code == 400
    assert MSG_INVALID in again.text
    _assert_hardened(again)
    assert len(deps.line.pushes) == 1


def test_get_does_not_disclose_validity() -> None:
    # token ที่ไม่มีอยู่จริงก็ได้หน้ายืนยันเหมือนกัน (ไม่บอกว่าใช้ได้ไหม)
    app, _ = make_test_app()
    page = TestClient(app).get("/close", params={"token": "nope"})
    assert page.status_code == 200
    assert MSG_CONFIRM in page.text
    assert MSG_INVALID not in page.text


def test_get_escapes_token() -> None:
    app, _ = make_test_app()
    evil = '"><script>alert(1)</script>'
    page = TestClient(app).get("/close", params={"token": evil})
    assert "<script>" not in page.text
    assert html.escape(evil, quote=True) in page.text


def test_missing_or_oversized_token_invalid() -> None:
    app, _ = make_test_app()
    client = TestClient(app)
    assert client.get("/close").status_code == 400
    assert client.get("/close", params={"token": "x" * 300}).status_code == 400
    assert _post(client, None).status_code == 400
    assert MSG_INVALID in _post(client, "nope").text
    assert _post(client, "nope").status_code == 400


async def test_post_after_other_path_already_ended() -> None:
    app, deps = make_test_app()
    conv, token = await _human_case(app, deps)
    # ลูกค้ากด "คุยกับ AI" ไปก่อน
    await deps.conversations.end_handoff(conv.id, HandoffEndReason.USER_RESUMED, conv.handoff_started_at)
    resp = _post(TestClient(app), token)
    assert resp.status_code == 200
    assert MSG_ALREADY_BOT in resp.text
    assert deps.line.pushes == []


async def test_old_round_token_does_not_close_new_round() -> None:
    app, deps = make_test_app()
    conv, old_token = await _human_case(app, deps)
    await deps.conversations.end_handoff(conv.id, HandoffEndReason.USER_RESUMED, conv.handoff_started_at)
    deps.clock.advance(minutes=10)
    assert await deps.conversations.start_handoff(conv.id, "again") is not None
    resp = _post(TestClient(app), old_token)
    assert MSG_ALREADY_BOT in resp.text
    assert (await deps.conversations.get(conv.id)).status is ConversationStatus.HUMAN
    assert deps.line.pushes == []


async def test_expired_token_invalid() -> None:
    app, deps = make_test_app()
    conv, token = await _human_case(app, deps)
    deps.clock.advance(deps.settings.close_token_ttl)
    resp = _post(TestClient(app), token)
    assert resp.status_code == 400
    assert MSG_INVALID in resp.text
    assert (await deps.conversations.get(conv.id)).status is ConversationStatus.HUMAN


async def test_send_failure_still_reports_closed() -> None:
    app, deps = make_test_app()
    conv, token = await _human_case(app, deps)

    async def broken(user_id: int) -> str | None:
        raise RuntimeError("db hiccup")

    deps.users.get_line_user_id = broken
    resp = _post(TestClient(app), token)
    assert MSG_CLOSED in resp.text
    assert (await deps.conversations.get(conv.id)).status is ConversationStatus.BOT

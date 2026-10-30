"""tests/pipeline/test_prepare.py — ขั้น prepare (app/pipeline/steps/prepare.py)

 - redact ข้อความขาเข้าก่อนเก็บ (ข้อความดิบไม่ลง DB)
 - conversation: bot เงียบเกิน STATE_TTL -> เปิดใหม่ (state_expired); human ไม่เปิดใหม่
 - state: หมดอายุ -> None + state_expired; ยังไม่หมด -> ใช้ state + counters
 - รูป: เก็บ MinIO + attachments (รวมระหว่าง human); พัง -> log แล้วไปต่อ
 - idempotent ต่อ raw_event_id (retry)
"""

from __future__ import annotations

import io
from datetime import timedelta

from PIL import Image
from structlog.testing import capture_logs

from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    ChatTurn,
    ConversationStatus,
    Counters,
    Direction,
    MediaContent,
    Product,
)
from app.pipeline.steps.prepare import CONSENT_TYPE, image_storage_key, prepare
from tests.fakes import make_deps, make_event

USER = "U-test"


def _db(deps: Deps):
    return deps.raw_events.db  # type: ignore[attr-defined]


def _png(size: tuple[int, int] = (2000, 1000)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 200, 30)).save(out, format="PNG")
    return out.getvalue()


def _inbound_rows(deps: Deps):
    return [m for m in _db(deps).messages.values() if m.direction is Direction.IN]


async def test_new_user_creates_conversation_run_and_stores_redacted_text() -> None:
    deps = make_deps()
    ctx = await prepare(deps, make_event(text="หาตู้เย็นค่ะ โทร 081-234-5678", raw_event_id=1))

    assert ctx.user.line_user_id == USER
    assert ctx.conversation.status is ConversationStatus.BOT
    assert ctx.reopened is False
    assert ctx.state is None and ctx.state_record is None and ctx.state_expired is False
    assert ctx.counters == Counters()
    assert ctx.text_redacted == "หาตู้เย็นค่ะ โทร [PHONE]"
    assert ctx.history == []
    assert ctx.has_image is False and ctx.image_keys == []
    assert ctx.has_consent is False

    (row,) = _inbound_rows(deps)
    assert row.id == ctx.inbound_message_id
    assert row.text_redacted == "หาตู้เย็นค่ะ โทร [PHONE]"
    assert "081" not in (row.text_redacted or "")
    assert row.message_type == "text"
    # touch_user_message
    assert _db(deps).conversations[ctx.conversation.id].last_user_message_at == deps.clock.now()
    # ai_runs หนึ่งแถวต่อ event
    assert list(_db(deps).ai_runs) == [ctx.ai_run_id]


async def test_postback_stored_without_text() -> None:
    deps = make_deps()
    ctx = await prepare(deps, make_event(postback="action=restart", raw_event_id=1))
    assert ctx.text_redacted is None
    (row,) = _inbound_rows(deps)
    assert row.message_type == "postback" and row.text_redacted is None


async def test_retry_same_event_is_idempotent() -> None:
    deps = make_deps()
    ev = make_event(text="ตู้เย็น", raw_event_id=7)
    a = await prepare(deps, ev)
    b = await prepare(deps, ev)
    assert a.inbound_message_id == b.inbound_message_id
    assert a.ai_run_id == b.ai_run_id
    assert a.conversation.id == b.conversation.id
    assert len(_inbound_rows(deps)) == 1
    # ข้อความของ event นี้ไม่อยู่ใน history ของตัวเอง แม้ retry
    assert b.history == []


async def test_history_excludes_current_and_is_redacted() -> None:
    deps = make_deps()
    await prepare(deps, make_event(text="ตู้เย็น อีเมล a@b.co", raw_event_id=1))
    deps.clock.advance(seconds=10)  # type: ignore[attr-defined]
    ctx = await prepare(deps, make_event(text="สีเขียว", raw_event_id=2))
    assert ctx.history == [ChatTurn(direction=Direction.IN, text_redacted="ตู้เย็น อีเมล [EMAIL]")]


async def test_reopen_after_idle_sets_state_expired() -> None:
    deps = make_deps()
    first = await prepare(deps, make_event(text="ตู้เย็น", raw_event_id=1))
    await deps.state.save(
        first.conversation.id,
        state={"category": "ตู้เย็น"},
        counters=Counters(clarify_streak=1),
        expires_at=deps.clock.now() + deps.settings.state_ttl,
    )
    deps.clock.advance(deps.settings.state_ttl + timedelta(minutes=1))  # type: ignore[attr-defined]

    ctx = await prepare(deps, make_event(text="เขียว", raw_event_id=2))
    assert ctx.reopened is True
    assert ctx.conversation.id != first.conversation.id
    assert ctx.state is None and ctx.state_expired is True
    assert ctx.counters == Counters()
    # conversation เก่าถูกปิด
    assert _db(deps).conversations[first.conversation.id].closed_at is not None
    # ข้อความของ conversation เก่าไม่ติดมาใน history
    assert ctx.history == []


async def test_human_never_reopens_after_idle() -> None:
    deps = make_deps()
    first = await prepare(deps, make_event(text="ตู้เย็น", raw_event_id=1))
    started = await deps.conversations.start_handoff(first.conversation.id, "test")
    assert started is not None
    deps.clock.advance(hours=3)  # type: ignore[attr-defined]  # เกิน STATE_TTL แต่ยังไม่ครบ 6 ชม.

    ctx = await prepare(deps, make_event(text="ยังอยู่ไหมคะ", raw_event_id=2))
    assert ctx.reopened is False
    assert ctx.conversation.id == first.conversation.id
    assert ctx.conversation.status is ConversationStatus.HUMAN
    # handoff_started_at ไม่ถูกเลื่อนโดยข้อความระหว่าง human
    conv = _db(deps).conversations[first.conversation.id]
    assert conv.handoff_started_at == started.handoff_started_at


async def test_expired_state_without_reopen() -> None:
    deps = make_deps()
    first = await prepare(deps, make_event(text="ตู้เย็น", raw_event_id=1))
    await deps.state.save(
        first.conversation.id,
        state={"category": "ตู้เย็น"},
        counters=Counters(clarify_streak=2, last_products=[Product(sku="A1", name="x", url="https://e.x/a")]),
        expires_at=deps.clock.now() + timedelta(minutes=30),
    )
    deps.clock.advance(minutes=40)  # type: ignore[attr-defined]  # conversation ยังไม่เงียบเกิน 2 ชม.

    ctx = await prepare(deps, make_event(text="เขียว", raw_event_id=2))
    assert ctx.reopened is False
    assert ctx.state_record is not None  # แถวยังอยู่ แต่ห้ามใช้
    assert ctx.state is None and ctx.state_expired is True
    assert ctx.counters == Counters()


async def test_live_state_and_counters_used() -> None:
    deps = make_deps()
    first = await prepare(deps, make_event(text="ตู้เย็น", raw_event_id=1))
    counters = Counters(clarify_streak=1)
    await deps.state.save(
        first.conversation.id,
        state={"category": "ตู้เย็น"},
        counters=counters,
        expires_at=deps.clock.now() + deps.settings.state_ttl,
    )
    deps.clock.advance(minutes=5)  # type: ignore[attr-defined]
    ctx = await prepare(deps, make_event(text="เขียว", raw_event_id=2))
    assert ctx.state == {"category": "ตู้เย็น"}
    assert ctx.counters == counters
    assert ctx.state_expired is False


async def test_image_is_resized_stored_and_recorded() -> None:
    deps = make_deps()
    deps.line.contents["m-1"] = MediaContent(data=_png(), content_type="image/png")  # type: ignore[attr-defined]
    ctx = await prepare(deps, make_event(message_type="image", raw_event_id=1))

    key = image_storage_key(ctx.user.id, 1)
    assert key == f"img/{ctx.user.id}/1.jpg"
    assert ctx.has_image is True and ctx.image_keys == [key]
    data, content_type = deps.storage.objects[key]  # type: ignore[attr-defined]
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) <= 1024
    (att,) = _db(deps).attachments.values()
    assert att["message_id"] == ctx.inbound_message_id
    assert att["storage_key"] == key and att["size_bytes"] == len(data)
    (row,) = _inbound_rows(deps)
    assert row.message_type == "image" and row.text_redacted is None


async def test_image_failure_is_logged_and_turn_continues() -> None:
    deps = make_deps()  # ไม่มี content -> get_content ยก LineApiError 404
    with capture_logs() as logs:
        ctx = await prepare(deps, make_event(message_type="image", raw_event_id=1))
    assert ctx.has_image is True and ctx.image_keys == []
    assert deps.storage.objects == {}  # type: ignore[attr-defined]
    assert _db(deps).attachments == {}
    assert len(_inbound_rows(deps)) == 1
    assert any(
        e["event"] == "prepare.image_store_failed" and e["error_class"] == "LineApiError"
        for e in logs
    )


async def test_unreadable_image_is_tolerated() -> None:
    deps = make_deps()
    deps.line.contents["m-1"] = MediaContent(data=b"not an image", content_type="image/jpeg")  # type: ignore[attr-defined]
    ctx = await prepare(deps, make_event(message_type="image", raw_event_id=1))
    assert ctx.image_keys == [] and deps.storage.objects == {}  # type: ignore[attr-defined]


async def test_image_retry_does_not_raise() -> None:
    deps = make_deps()
    deps.line.contents["m-1"] = MediaContent(data=_png((50, 50)), content_type="image/png")  # type: ignore[attr-defined]
    ev = make_event(message_type="image", raw_event_id=1)
    await prepare(deps, ev)
    again = await prepare(deps, ev)  # attachments ชน UNIQUE -> log แล้วไปต่อ
    assert len(_db(deps).attachments) == 1
    assert again.has_image is True


async def test_image_during_human_is_stored() -> None:
    deps = make_deps()
    first = await prepare(deps, make_event(text="ตู้เย็น", raw_event_id=1))
    await deps.conversations.start_handoff(first.conversation.id, "test")
    deps.line.contents["m-2"] = MediaContent(data=_png((40, 40)), content_type="image/png")  # type: ignore[attr-defined]
    ctx = await prepare(deps, make_event(message_type="image", raw_event_id=2))
    assert ctx.conversation.status is ConversationStatus.HUMAN
    assert ctx.image_keys == [image_storage_key(ctx.user.id, 2)]
    assert len(_db(deps).attachments) == 1


async def test_consent_is_read_but_never_blocks() -> None:
    deps = make_deps()
    first = await prepare(deps, make_event(text="ตู้เย็น", raw_event_id=1))
    assert first.has_consent is False
    deps.users.add_consent(first.user.id, CONSENT_TYPE, True)  # type: ignore[attr-defined]
    ctx = await prepare(deps, make_event(text="สีขาว", raw_event_id=2))
    assert ctx.has_consent is True

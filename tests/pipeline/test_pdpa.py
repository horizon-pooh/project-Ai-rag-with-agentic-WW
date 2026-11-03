"""tests/pipeline/test_pdpa.py

เคสทดสอบของ app/pipeline/pdpa.py (fake ทั้งหมด)

รายละเอียด:
 - ลบสำเร็จ: ไฟล์ใน Storage ถูกลบครบ, ข้อมูลใน DB ของ user นี้หายทั้งสาย (raw events ด้วย) ของ user อื่นยังอยู่
 - ลบไฟล์ไม่ได้: DB ยังถูกลบ, key ที่พังคืนใน failed_keys ไม่ยก error
 - ไม่มี user: user_found=False แต่ raw events ของ id นี้ยังถูกลบ
 - log ไม่มี line_user_id ตัวจริง (มีแค่ user_ref)

แนวคิดจาก: ใหม่ (R2-A)
สถานะ: มีโค้ดแล้ว (R2-A)
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog.testing

from app.pipeline import pdpa
from app.pipeline.schemas import AiStepRecord, FeedbackKind, hash_token
from tests.fakes import DEFAULT_NOW, make_deps

LINE_ID = "U0123456789abcdef0123456789abcdef"


async def _seed_user(deps: Any, line_user_id: str, n_images: int = 2) -> list[str]:
    """user + conversation + ข้อความมีรูป + ai run/step + feedback + consent + raw event; คืน storage keys"""
    rid = await deps.raw_events.insert(
        webhook_event_id=f"wh-{line_user_id}",
        line_user_id=line_user_id,
        event_type="message",
        payload={},
        occurred_at=DEFAULT_NOW,
    )
    user = await deps.users.get_or_create(line_user_id)
    deps.users.add_consent(user.id, "privacy", True)
    conv, _ = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)
    keys = []
    for i in range(n_images):
        mid = await deps.messages.add_inbound(
            conversation_id=conv.id,
            raw_event_id=rid * 100 + i,
            message_type="image",
            text_redacted=None,
        )
        key = f"images/{conv.id}/m-{i}"
        await deps.storage.put_image(key, b"x", "image/jpeg")
        await deps.messages.add_attachment(
            message_id=mid, storage_key=key, content_type="image/jpeg", size_bytes=1
        )
        keys.append(key)
    run = await deps.ai_runs.create_run(raw_event_id=rid, conversation_id=conv.id)
    await deps.ai_runs.add_step(
        AiStepRecord(ai_run_id=run, raw_event_id=rid, seq=1, step="call_ai")
    )
    await deps.feedback.add(conversation_id=conv.id, kind=FeedbackKind.THUMB_UP, ai_run_id=run)
    return keys


async def test_delete_removes_db_rows_and_files() -> None:
    deps = make_deps()
    keys = await _seed_user(deps, LINE_ID)
    other_keys = await _seed_user(deps, "U-other", n_images=1)
    db = deps.users.db

    result = await pdpa.delete_user_data(deps, LINE_ID)

    assert result == pdpa.PdpaResult(user_found=True, files_deleted=2, failed_keys=[])
    assert sorted(deps.storage.deleted) == sorted(keys)
    assert set(deps.storage.objects) == set(other_keys)
    assert [u.line_user_id for u in db.users.values()] == ["U-other"]
    assert [r.line_user_id for r in db.raw_events.values()] == ["U-other"]
    other_uid = next(iter(db.users))
    assert all(c.user_id == other_uid for c in db.conversations.values())
    assert {c["user_id"] for c in db.consents} == {other_uid}
    assert len(db.messages) == 1 and len(db.attachments) == 1
    assert len(db.ai_runs) == 1 and len(db.ai_steps) == 1 == len(db.ai_step_created_at)
    assert len(db.feedback) == 1


async def test_storage_failure_reported_not_raised() -> None:
    deps = make_deps()
    keys = await _seed_user(deps, LINE_ID)
    deps.storage.fail_delete.add(keys[0])

    result = await pdpa.delete_user_data(deps, LINE_ID)

    assert result.user_found is True
    assert result.files_deleted == 1
    assert result.failed_keys == [keys[0]]
    # DB เป็นความจริง: ลบแล้วแม้ไฟล์ยังค้าง
    assert deps.users.db.users == {}
    assert keys[0] in deps.storage.objects


async def test_already_deleted_attachment_not_returned() -> None:
    deps = make_deps()
    keys = await _seed_user(deps, LINE_ID)
    db = deps.users.db
    first = next(a for a in db.attachments.values() if a["storage_key"] == keys[0])
    first["deleted_at"] = DEFAULT_NOW  # retention ลบไฟล์ไปแล้ว

    result = await pdpa.delete_user_data(deps, LINE_ID)
    assert result.files_deleted == 1
    assert deps.storage.deleted == [keys[1]]


async def test_unknown_user_still_deletes_raw_events() -> None:
    deps = make_deps()
    await deps.raw_events.insert(
        webhook_event_id="wh-x",
        line_user_id=LINE_ID,
        event_type="follow",
        payload={},
        occurred_at=DEFAULT_NOW,
    )
    result = await pdpa.delete_user_data(deps, LINE_ID)
    assert result == pdpa.PdpaResult(user_found=False)
    assert deps.raw_events.db.raw_events == {}
    assert deps.storage.deleted == []


async def test_empty_id_rejected() -> None:
    with pytest.raises(ValueError):
        await pdpa.delete_user_data(make_deps(), "")


async def test_logs_hash_not_raw_id() -> None:
    deps = make_deps()
    keys = await _seed_user(deps, LINE_ID)
    deps.storage.fail_delete.add(keys[0])
    with structlog.testing.capture_logs() as logs:
        await pdpa.delete_user_data(deps, LINE_ID)
        await pdpa.delete_user_data(deps, LINE_ID)
    assert logs
    assert LINE_ID not in repr(logs)
    assert all(e["user_ref"] == hash_token(LINE_ID)[:12] for e in logs)
    assert pdpa.user_ref(LINE_ID) == hash_token(LINE_ID)[:12]

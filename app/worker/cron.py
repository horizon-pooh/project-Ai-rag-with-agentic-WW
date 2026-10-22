"""app/worker/cron.py

งานเบื้องหลังตามเวลา

รายละเอียด:
 - requeue_pending (ทุก 1-2 นาที): หา line_raw_events ที่ค้าง pending นานเกินควร ใส่คิวใหม่ด้วย job id เดิม; จำนวนที่เจอใช้แจ้งเตือนได้
 - handoff_timeout (ทุกไม่กี่นาที): conversation ที่ human และ now - handoff_started_at >= HANDOFF_TTL_HOURS -> end_handoff(reason='timeout')
 - handoff_check ในขั้น 4 เช็คซ้ำเป็นตาข่าย ทั้งสองทางใช้ end_handoff ตัวเดียว (ไม่ส่งซ้ำ)
 - retention (วันละครั้ง): raw 90 วัน, ข้อความและ trace 12 เดือนแล้ว anonymize, รูปเก็บสั้นกว่า (ลบใน MinIO แยก)
 - (เสนอ) รายงานรายวัน ยังไม่อยู่ในรอบแรก

ข้อตัดสินในงาน 8:
 - release_stuck (ทุก 2 นาที): processing ค้างนานกว่า TTL ล็อก + ช่องเผื่อ (worker ตายกลางทาง) -> pending แล้วใส่คิว
 - handoff_timeout สำเร็จ -> push ข้อความ HANDOFF_TIMED_OUT_PREFIX + EMPTY ผ่าน sendGuard
   dedupe_key ผูกกับรอบส่งต่อ (conversation + handoff_started_at) -> รันซ้ำ/ชนกับ handoff_check ก็ไม่ส่งซ้ำ
 - ctx ที่ใช้: deps, redis (ArqRedis ของ arq ใช้ใส่คิว), user_lookup, raw_retention (ตั้งใน settings.on_startup)

แนวคิดจาก: src/jobs/, src/ops/dailyReport.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8) — retention ทำแค่ลบ raw 90 วัน; anonymize 12 เดือนและลบรูปใน MinIO ยังเป็น TODO
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

import structlog

from app.pipeline import handoff, replies
from app.pipeline.ports import Deps
from app.pipeline.schemas import HandoffEndReason
from app.pipeline.steps.send import send
from app.worker.locks import LOCK_TTL_MARGIN_S
from app.worker.tasks import enqueue_event

log = structlog.get_logger(__name__)

# pending ที่รับมานานกว่านี้ถือว่าหลุดคิว (Redis/worker พัง หรือใส่คิวไม่สำเร็จ)
STALE_PENDING_AFTER = timedelta(minutes=2)
# processing ค้างนานกว่า TTL ล็อก (TURN_DEADLINE_S + 30) + ช่องเผื่อนี้ = worker ตายกลางทาง
STUCK_EXTRA_MARGIN = timedelta(seconds=60)
SWEEP_LIMIT = 500
HANDOFF_TIMEOUT_LIMIT = 100

RAW_RETENTION = timedelta(days=90)
RETENTION_BATCH = 5000
# จำกัดรอบต่อครั้ง กันงานยาวเกิน (ที่เหลือทำวันถัดไป)
RETENTION_MAX_BATCHES = 200

HANDOFF_TIMEOUT_PURPOSE = "handoff_timeout"


class LineUserLookup(Protocol):
    """หา line_user_id จาก conversation (repositories/lookups.py) — ไม่อยู่ในพอร์ตของ pipeline"""

    async def line_user_id_for_conversation(self, conversation_id: int) -> str | None: ...


class RawRetention(Protocol):
    """repositories/raw_events.PgRawEventsRepo.delete_older_than (ไม่อยู่ในพอร์ต)"""

    async def delete_older_than(self, cutoff: datetime, limit: int = ...) -> int: ...


def stuck_after(turn_deadline_s: float) -> timedelta:
    """processing นานกว่านี้ถือว่าค้าง: TTL ล็อก (TURN_DEADLINE_S + 30 วิ) + ช่องเผื่อ"""
    return timedelta(seconds=turn_deadline_s + LOCK_TTL_MARGIN_S) + STUCK_EXTRA_MARGIN


def handoff_timeout_dedupe_key(conversation_id: int, started_at: datetime) -> str:
    return f"out:handoff_timeout:{conversation_id}:{started_at.isoformat()}"


def handoff_timeout_text() -> str:
    return f"{replies.HANDOFF_TIMED_OUT_PREFIX} {replies.EMPTY}"


async def requeue_pending(ctx: dict[str, Any]) -> int:
    """pending ที่ค้างเกิน STALE_PENDING_AFTER -> ใส่คิวใหม่ (job id เดิม arq กันซ้ำให้) คืนจำนวนที่เจอ"""
    deps: Deps = ctx["deps"]
    ids = await deps.raw_events.list_stale(deps.clock.now() - STALE_PENDING_AFTER, limit=SWEEP_LIMIT)
    if not ids:
        return 0
    enqueued = 0
    for rid in ids:
        try:
            if await enqueue_event(ctx["redis"], rid):
                enqueued += 1
        except Exception:
            log.exception("requeue_enqueue_failed", raw_event_id=rid)
    # สัญญาณแจ้งเตือน: ปกติควรเป็น 0 (แปลว่าคิวหลุดหรือ worker ไม่ทัน)
    log.warning("requeue_pending_found", count=len(ids), enqueued=enqueued)
    return len(ids)


async def release_stuck(ctx: dict[str, Any]) -> int:
    """processing ที่ค้างนานเกินอายุล็อก -> pending แล้วใส่คิวใหม่ คืนจำนวนที่ปล่อย"""
    deps: Deps = ctx["deps"]
    cutoff = deps.clock.now() - stuck_after(deps.settings.turn_deadline_s)
    ids = await deps.raw_events.release_stuck(cutoff, limit=SWEEP_LIMIT)
    if not ids:
        return 0
    for rid in ids:
        try:
            await enqueue_event(ctx["redis"], rid)
        except Exception:
            # ไม่เป็นไร: แถวเป็น pending แล้ว requeue_pending เก็บรอบหน้า
            log.exception("release_stuck_enqueue_failed", raw_event_id=rid)
    log.warning("release_stuck_found", count=len(ids))
    return len(ids)


async def handoff_timeout(ctx: dict[str, Any]) -> int:
    """human ที่ครบ HANDOFF_TTL_HOURS นับจาก handoff_started_at -> end_handoff(TIMEOUT) แล้วบอกลูกค้า

    คืนจำนวนที่ปิดสำเร็จ; end_handoff ได้ None (ทางอื่นปิดไปก่อน) -> ไม่ส่งอะไร
    """
    deps: Deps = ctx["deps"]
    lookup: LineUserLookup = ctx["user_lookup"]
    convs = await deps.conversations.list_handoff_timed_out(
        deps.settings.handoff_ttl, limit=HANDOFF_TIMEOUT_LIMIT
    )
    closed = 0
    for conv in convs:
        started_at = conv.handoff_started_at
        if started_at is None:
            continue
        try:
            new = await handoff.end_handoff(deps, conv, HandoffEndReason.TIMEOUT)
            if new is None:
                continue
            closed += 1
            line_user_id = await lookup.line_user_id_for_conversation(conv.id)
            if line_user_id is None:
                log.warning("handoff_timeout_no_line_user", conversation_id=conv.id)
                continue
            text = handoff_timeout_text()
            result = await send(
                deps,
                conversation_id=conv.id,
                line_user_id=line_user_id,
                raw_event_id=None,
                outbound=replies.outbound_text(HANDOFF_TIMEOUT_PURPOSE, text),
                dedupe_key=handoff_timeout_dedupe_key(conv.id, started_at),
                text_redacted=text,
            )
            log.info("handoff_timeout_closed", conversation_id=conv.id, send=result.status.value)
        except Exception:
            # เคสหนึ่งพังไม่หยุดเคสอื่น; ยังเป็น human -> รอบหน้าลองใหม่, ปิดแล้วแต่ส่งไม่ได้ -> log
            log.exception("handoff_timeout_failed", conversation_id=conv.id)
    return closed


async def retention(ctx: dict[str, Any]) -> int:
    """ลบ line_raw_events ที่เกิน 90 วันทีละก้อน คืนจำนวนที่ลบ"""
    deps: Deps = ctx["deps"]
    repo: RawRetention = ctx["raw_retention"]
    cutoff = deps.clock.now() - RAW_RETENTION
    total = 0
    for _ in range(RETENTION_MAX_BATCHES):
        n = await repo.delete_older_than(cutoff, limit=RETENTION_BATCH)
        total += n
        if n < RETENTION_BATCH:
            break
    # TODO(retention): messages / ai_steps อายุเกิน 12 เดือน -> anonymize (ยังไม่มี SQL ใน repositories)
    # TODO(retention): attachments เกินอายุเก็บรูป -> Storage.delete แล้วตั้ง deleted_at (อายุเก็บรูปยังไม่ตัดสิน)
    log.info("retention_raw_deleted", count=total)
    return total


__all__ = [
    "RAW_RETENTION",
    "STALE_PENDING_AFTER",
    "handoff_timeout",
    "handoff_timeout_dedupe_key",
    "handoff_timeout_text",
    "release_stuck",
    "requeue_pending",
    "retention",
    "stuck_after",
]

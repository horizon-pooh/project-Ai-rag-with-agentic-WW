"""app/worker/cron.py

งานเบื้องหลังตามเวลา

รายละเอียด:
 - requeue_pending (ทุก 1-2 นาที): หา line_raw_events ที่ค้าง pending นานเกินควร ใส่คิวใหม่ด้วย job id เดิม; จำนวนที่เจอใช้แจ้งเตือนได้
 - handoff_timeout (ทุกไม่กี่นาที): conversation ที่ human และ now - handoff_started_at >= HANDOFF_TTL_HOURS -> end_handoff(reason='timeout')
 - handoff_check ในขั้น 4 เช็คซ้ำเป็นตาข่าย ทั้งสองทางใช้ end_handoff ตัวเดียว (ไม่ส่งซ้ำ)
 - retention (วันละครั้ง): raw 90 วัน, ข้อความและ trace TRACE_RETENTION_MONTHS (12) เดือนแล้ว anonymize,
   รูปเก็บสั้นกว่า IMAGE_RETENTION_DAYS (เสนอ 30 วัน) ลบไฟล์ใน MinIO แล้วตั้ง attachments.deleted_at
 - (เสนอ) รายงานรายวัน ยังไม่อยู่ในรอบแรก

ข้อตัดสินในงาน 8:
 - release_stuck (ทุก 2 นาที): processing ค้างนานกว่า TTL ล็อก + ช่องเผื่อ (worker ตายกลางทาง) -> pending แล้วใส่คิว
 - handoff_timeout สำเร็จ -> push ข้อความ HANDOFF_TIMED_OUT_PREFIX + EMPTY ผ่าน sendGuard
   dedupe_key ผูกกับรอบส่งต่อ (conversation + handoff_started_at) -> รันซ้ำ/ชนกับ handoff_check ก็ไม่ส่งซ้ำ
 - ctx ที่ใช้: deps, redis (ArqRedis ของ arq ใช้ใส่คิว), raw_retention (ตั้งใน settings.on_startup)
 - หา line_user_id ของเคสผ่าน deps.users.get_line_user_id (ย้ายเข้าพอร์ตแล้วใน R2-A)

ข้อตัดสินใน R2-A (retention):
 - แต่ละส่วน (raw / รูป / anonymize) พังไม่หยุดส่วนอื่น (log แล้วไปต่อ รอบหน้าทำต่อ)
 - รูป: Storage.delete ก่อนแล้วจึง mark_attachment_deleted; ลบไฟล์ไม่ได้ -> ไม่ mark (รอบหน้าลองใหม่)
   ก้อนที่มีตัวพังหยุดวนก้อนถัดไป (ตัวพังจะกลับมาหัวแถวอีก กันวนไม่จบ)
 - anonymize: ตัดเดือนตามปฏิทิน (months_before) ทำทีละก้อนจนได้ < RETENTION_BATCH; รันซ้ำได้ 0

แนวคิดจาก: src/jobs/, src/ops/dailyReport.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8 + R2-A: retention ครบ raw / รูป / anonymize)
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
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
# รูปต่อก้อน (แต่ละตัวเรียก Storage ทีละครั้ง)
IMAGE_RETENTION_BATCH = 500

HANDOFF_TIMEOUT_PURPOSE = "handoff_timeout"


class RawRetention(Protocol):
    """repositories/raw_events.PgRawEventsRepo.delete_older_than (ไม่อยู่ในพอร์ต)"""

    async def delete_older_than(self, cutoff: datetime, limit: int = ...) -> int: ...


@dataclass(frozen=True)
class RetentionResult:
    """จำนวนที่ทำในรอบนี้ (ไม่มีข้อมูลส่วนบุคคล)"""

    raw_deleted: int = 0
    images_deleted: int = 0
    images_failed: int = 0
    messages_anonymized: int = 0
    ai_steps_anonymized: int = 0


def months_before(dt: datetime, months: int) -> datetime:
    """ย้อนไป months เดือนตามปฏิทิน (วันที่เกินสิ้นเดือน -> วันสุดท้ายของเดือนนั้น) เวลาเดิม timezone เดิม"""
    total = dt.year * 12 + (dt.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


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
            line_user_id = await deps.users.get_line_user_id(conv.user_id)
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


async def _delete_raw(ctx: dict[str, Any], now: datetime) -> int:
    repo: RawRetention = ctx["raw_retention"]
    cutoff = now - RAW_RETENTION
    total = 0
    for _ in range(RETENTION_MAX_BATCHES):
        n = await repo.delete_older_than(cutoff, limit=RETENTION_BATCH)
        total += n
        if n < RETENTION_BATCH:
            break
    return total


async def _delete_images(deps: Deps, now: datetime) -> tuple[int, int]:
    """คืน (ลบสำเร็จ, ลบไฟล์ไม่ได้)"""
    cutoff = now - deps.settings.image_retention
    deleted = failed = 0
    for _ in range(RETENTION_MAX_BATCHES):
        rows = await deps.messages.list_expired_attachments(cutoff, limit=IMAGE_RETENTION_BATCH)
        batch_failed = 0
        for att in rows:
            try:
                await deps.storage.delete(att.storage_key)
            except Exception:
                # แถวคงไว้ (deleted_at ยัง NULL) -> รอบหน้าลองใหม่
                batch_failed += 1
                log.exception("retention_image_delete_failed", attachment_id=att.id)
                continue
            await deps.messages.mark_attachment_deleted(att.id)
            deleted += 1
        failed += batch_failed
        if len(rows) < IMAGE_RETENTION_BATCH or batch_failed:
            break
    return deleted, failed


async def _anonymize(deps: Deps, now: datetime) -> tuple[int, int]:
    """คืน (messages, ai_steps) ที่ anonymize ในรอบนี้"""
    cutoff = months_before(now, deps.settings.trace_retention_months)
    counts: list[int] = []
    for fn in (deps.messages.anonymize_older_than, deps.ai_runs.anonymize_steps_older_than):
        total = 0
        for _ in range(RETENTION_MAX_BATCHES):
            n = await fn(cutoff, limit=RETENTION_BATCH)
            total += n
            if n < RETENTION_BATCH:
                break
        counts.append(total)
    return counts[0], counts[1]


async def retention(ctx: dict[str, Any]) -> RetentionResult:
    """วันละครั้ง: ลบ raw เกิน 90 วัน, ลบรูปเกิน IMAGE_RETENTION_DAYS, anonymize เกิน TRACE_RETENTION_MONTHS

    แต่ละส่วนพังไม่หยุดส่วนอื่น (log แล้วไปต่อ) คืนจำนวนของแต่ละส่วน
    """
    deps: Deps = ctx["deps"]
    now = deps.clock.now()
    raw = images = images_failed = msgs = steps = 0
    try:
        raw = await _delete_raw(ctx, now)
    except Exception:
        log.exception("retention_raw_failed")
    try:
        images, images_failed = await _delete_images(deps, now)
    except Exception:
        log.exception("retention_images_failed")
    try:
        msgs, steps = await _anonymize(deps, now)
    except Exception:
        log.exception("retention_anonymize_failed")
    result = RetentionResult(
        raw_deleted=raw,
        images_deleted=images,
        images_failed=images_failed,
        messages_anonymized=msgs,
        ai_steps_anonymized=steps,
    )
    log.info(
        "retention_done",
        raw_deleted=raw,
        images_deleted=images,
        images_failed=images_failed,
        messages_anonymized=msgs,
        ai_steps_anonymized=steps,
    )
    return result


__all__ = [
    "RAW_RETENTION",
    "STALE_PENDING_AFTER",
    "RetentionResult",
    "handoff_timeout",
    "handoff_timeout_dedupe_key",
    "handoff_timeout_text",
    "months_before",
    "release_stuck",
    "requeue_pending",
    "retention",
    "stuck_after",
]

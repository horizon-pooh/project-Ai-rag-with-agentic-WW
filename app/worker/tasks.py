"""app/worker/tasks.py

process_event(raw_event_id): งานหลักหนึ่งชิ้นต่อหนึ่ง event + enqueue_event (ใส่คิว procrastinate)

รายละเอียด:
 - ทำ event ที่ค้างของ user ตามลำดับเวลา (เก่าสุดก่อน)
 - ตรวจ process_status กันรันซ้ำ (คิวรันซ้ำได้เมื่อ retry)
 - สร้างตัวช่วย (repositories, services) แล้วส่งให้ pipeline.orchestrator
 - ตั้ง raw_event_id / ai_run_id ใน log context
 - จบแล้วตั้ง process_status = done หรือ failed (บันทึก error เสมอ)

ข้อตัดสินในงาน 8:
 - ตัวช่วยสร้างครั้งเดียวตอน worker เริ่ม (worker/deps.py) แล้วส่งเข้างานทาง ctx ไม่สร้างใหม่ทุกงาน
 - claim (pending -> processing แบบมีเงื่อนไข) คือด่านกันรันซ้ำ: ได้ None = มีคนทำแล้ว (fast path / งานอื่น) ข้าม
 - พังระหว่าง run_turn: attempts < 3 -> release (กลับเป็น pending) แล้วให้คิวลองงานนี้ใหม่ (ไม่ทำ event ใหม่กว่า
   ก่อนตัวที่พัง เพื่อรักษาลำดับ); attempts ครบ 3 -> failed แล้วทำตัวถัดไป
 - เพดานเวลาของงาน (job_timeout) ใช้ได้ราวหนึ่งรอบ AI: ทำต่อเฉพาะเมื่อเวลาที่เหลือพอสำหรับอีกหนึ่ง
   TURN_DEADLINE_S ไม่งั้นหยุดไว้ให้งานของ event นั้นเอง (หรือ cron) มาทำต่อ
 - ไม่ถือ transaction DB ข้าม run_turn: repository ทุกเมธอดเปิด-ปิด transaction เอง

ข้อตัดสินใน D-010 (arq+Redis -> procrastinate บน Postgres):
 - ล็อกต่อ user = `lock` ของ procrastinate: งานที่ lock เดียวกันรันทีละงาน เรียงตาม priority แล้วเวลาสร้าง
   (เอกสาร procrastinate howto/advanced/locks) — แทน worker/locks.py (Redis SET NX PX) ที่ลบไปแล้ว
   lock = "user:" + pdpa.user_ref(line_user_id) (hash ตัดสั้น) ไม่ใส่ line_user_id ตัวจริงลงตาราง procrastinate_jobs
   (ลบตาม PDPA ไม่ต้องตามไปลบในตารางของไลบรารี); hash ชนกัน = แค่สอง user รอคิวกัน ไม่ผิด
 - event ไม่มี user (เช่นจากกลุ่ม) -> ไม่มี lock
 - กันใส่คิวซ้ำ = `queueing_lock` "raw:{raw_event_id}" (ซ้ำได้แค่ตอนงานเดิมยังรอในคิว todo): AlreadyEnqueued
   -> ถือว่าอยู่ในคิวแล้ว คืน False; งานเดิมกำลังทำ (doing) อยู่ใส่ใหม่ได้ (การกันทำซ้ำจริงอยู่ที่ claim ใน DB)
 - ต้องลองใหม่ -> ยก EventRetry; RetryStrategy ของงาน (worker/app.py) เลื่อนไป RETRY_WAIT_S แล้วรันงานเดิม
   ระหว่างนั้นงานที่ใหม่กว่าของ user เดียวกันรอ (lock เรียงตามเวลาสร้าง งานที่ retry สร้างก่อน)
 - ไม่ต้องรอล็อกเองอีกแล้ว (procrastinate ไม่หยิบงานที่ lock ยังมีงาน doing)

แนวคิดจาก: src/jobs/softnixWorker.js, src/pipeline/userQueue.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8 + D-010)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from procrastinate.exceptions import AlreadyEnqueued

from app.logging import bind_ids, clear_ids
from app.pipeline import orchestrator
from app.pipeline.pdpa import user_ref
from app.pipeline.ports import Deps
from app.pipeline.schemas import ProcessStatus, RawEvent
from app.privacy.redact import redact_text

if TYPE_CHECKING:
    from procrastinate import App

    from app.pipeline.orchestrator import PipelineConfig

log = structlog.get_logger(__name__)

# ชื่องานใน procrastinate (worker/app.py ลงทะเบียนด้วยชื่อนี้)
PROCESS_EVENT_TASK = "process_event"
QUEUEING_LOCK_PREFIX = "raw:"
USER_LOCK_PREFIX = "user:"

# event หนึ่งลองได้กี่ครั้ง (นับจาก attempts ที่ claim เพิ่มให้) ครบแล้ว -> failed
MAX_EVENT_ATTEMPTS = 3
# job_timeout = TURN_DEADLINE_S + เท่านี้ (worker/app.py ใช้ค่านี้)
JOB_TIMEOUT_MARGIN_S = 60
# ช่องเผื่อเวลาท้ายงานสำหรับ mark_done
BATCH_TAIL_MARGIN_S = 10
# เริ่ม event ถัดไปในงานเดียวกันได้เมื่อใช้เวลาไปไม่เกินนี้: เวลาที่ใช้ + TURN_DEADLINE_S + ช่องท้าย <= job_timeout
BATCH_BUDGET_S = JOB_TIMEOUT_MARGIN_S - BATCH_TAIL_MARGIN_S
ERROR_MAX_CHARS = 500


class EventRetry(Exception):
    """event ถูก release กลับเป็น pending แล้ว — ให้คิวลองงานนี้ใหม่ (RetryStrategy ใน worker/app.py)"""


def queueing_lock_for(raw_event_id: int) -> str:
    return f"{QUEUEING_LOCK_PREFIX}{raw_event_id}"


def user_lock_for(line_user_id: str | None) -> str | None:
    """lock ต่อ user ของ procrastinate; ไม่มี user -> None (ไม่ล็อก)"""
    if not line_user_id:
        return None
    return f"{USER_LOCK_PREFIX}{user_ref(line_user_id)}"


async def enqueue_event(queue: App, raw_event_id: int, line_user_id: str | None) -> bool:
    """ใส่คิว process_event(raw_event_id) ด้วย lock ต่อ user + queueing_lock ต่อ raw event

    คืน False ถ้ามีงานของ raw event นี้รออยู่ในคิวแล้ว (AlreadyEnqueued); error อื่นปล่อยให้ผู้เรียกจับ
    (webhook / cron log แล้วปล่อยแถว pending ให้ cron requeue_pending เก็บ)
    queue = procrastinate App ที่เปิดแล้ว (worker/app.py) — เทสต์ใช้ App บน InMemoryConnector
    """
    deferrer = queue.configure_task(
        PROCESS_EVENT_TASK,
        lock=user_lock_for(line_user_id),
        queueing_lock=queueing_lock_for(raw_event_id),
    )
    try:
        await deferrer.defer_async(raw_event_id=raw_event_id)
    except AlreadyEnqueued:
        return False
    return True


def error_text(exc: BaseException) -> str:
    """ข้อความ error ที่ลง line_raw_events.error: redact แล้ว ตัดความยาวแล้ว"""
    try:
        text = redact_text(f"{type(exc).__name__}: {exc}")
    except Exception:
        text = type(exc).__name__
    return text[:ERROR_MAX_CHARS]


def _final_status(outcome: orchestrator.TurnOutcome) -> ProcessStatus:
    """process_status ตอนจบ: route skipped_event (event ที่ไม่ทำ เช่น ไม่มี userId) -> SKIPPED ไม่งั้น DONE"""
    if outcome.route == orchestrator.TurnRoute.SKIPPED_EVENT.value:
        return ProcessStatus.SKIPPED
    return ProcessStatus.DONE


async def run_claimed(deps: Deps, raw: RawEvent, cfg: PipelineConfig) -> bool:
    """รัน pipeline กับ event ที่ claim มาแล้ว แล้วตั้งผลจบ (worker และ fast path ใช้ร่วม)

    คืน True = จบแล้ว (done/skipped/failed); False = ปล่อยกลับเป็น pending ให้ลองใหม่
    run_turn ไม่ปล่อย AiError ออกมา; error อื่น -> attempts < MAX_EVENT_ATTEMPTS: release, ไม่งั้น failed
    """
    try:
        outcome = await orchestrator.run_turn(deps, raw, cfg)
    except Exception as e:
        err = error_text(e)
        if raw.attempts < MAX_EVENT_ATTEMPTS:
            await deps.raw_events.release(raw.id, err)
            log.warning(
                "event_released_for_retry",
                attempts=raw.attempts,
                error_class=type(e).__name__,
                exc_info=True,
            )
            return False
        await deps.raw_events.mark_failed(raw.id, err)
        log.error(
            "event_failed", attempts=raw.attempts, error_class=type(e).__name__, exc_info=True
        )
        return True
    status = _final_status(outcome)
    await deps.raw_events.mark_done(raw.id, status)
    log.info("event_done", status=status.value)
    return True


async def process_event(ctx: dict[str, Any], raw_event_id: int) -> None:
    """ทำ event นี้และ event ที่ค้างของ user เดียวกันตามลำดับ (งานของ user เดียวกันไม่รันซ้อน: lock ของ procrastinate)

    ctx ต้องมี deps (Deps), pipeline_cfg (PipelineConfig) — worker/app.py ส่งมาทาง additional_context
    ต้องลองใหม่ -> ยก EventRetry
    """
    deps: Deps = ctx["deps"]
    cfg: PipelineConfig = ctx["pipeline_cfg"]
    clear_ids()
    bind_ids(raw_event_id=raw_event_id)
    try:
        raw = await deps.raw_events.get(raw_event_id)
        if raw is None:
            log.warning("event_missing")
            return

        if raw.line_user_id is None:
            # ไม่มีเจ้าของ (เช่น event จากกลุ่ม) -> ไม่ต้องเรียงลำดับต่อ user (งานนี้ไม่มี lock)
            claimed = await deps.raw_events.claim(raw_event_id)
            if claimed is None:
                log.info("event_claim_skipped")
                return
            if not await run_claimed(deps, claimed, cfg):
                raise EventRetry
            return

        # ไม่ข้ามแม้ event ของงานนี้จะเสร็จแล้ว: retry ของงานนี้อาจมาเพื่อ event อื่นของ user ที่ถูก release
        needs_retry = False
        started = time.monotonic()
        pending = await deps.raw_events.list_pending_ids_for_user(raw.line_user_id)
        for i, rid in enumerate(pending):
            if i > 0 and time.monotonic() - started > BATCH_BUDGET_S:
                # เวลาไม่พออีกหนึ่งรอบ AI -> เหลือไว้ให้งานของ event นั้นเอง / cron
                log.info("batch_budget_reached", remaining=len(pending) - i)
                break
            clear_ids()
            bind_ids(raw_event_id=rid)
            claimed = await deps.raw_events.claim(rid)
            if claimed is None:
                # fast path หรืองานอื่นทำไปแล้ว
                log.info("event_claim_skipped")
                continue
            if not await run_claimed(deps, claimed, cfg):
                # รักษาลำดับ: ไม่ทำ event ที่ใหม่กว่าก่อนตัวที่ต้องลองใหม่
                needs_retry = True
                break
        if needs_retry:
            raise EventRetry
    finally:
        clear_ids()


__all__ = [
    "JOB_TIMEOUT_MARGIN_S",
    "MAX_EVENT_ATTEMPTS",
    "PROCESS_EVENT_TASK",
    "QUEUEING_LOCK_PREFIX",
    "USER_LOCK_PREFIX",
    "EventRetry",
    "enqueue_event",
    "error_text",
    "process_event",
    "queueing_lock_for",
    "run_claimed",
    "user_lock_for",
]

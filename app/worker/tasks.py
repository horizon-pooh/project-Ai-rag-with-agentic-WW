"""app/worker/tasks.py

process_event(raw_event_id): งานหลักหนึ่งชิ้นต่อหนึ่ง event

รายละเอียด:
 - จับล็อกต่อ user (locks.py) ก่อนทำอะไร
 - ทำ event ที่ค้างของ user ตามลำดับเวลา (เก่าสุดก่อน)
 - ตรวจ process_status กันรันซ้ำ (คิวรันซ้ำได้เมื่อ retry)
 - สร้างตัวช่วย (repositories, services) แล้วส่งให้ pipeline.orchestrator
 - ตั้ง raw_event_id / ai_run_id ใน log context
 - จบแล้วตั้ง process_status = done หรือ failed (บันทึก error เสมอ)

ข้อตัดสินในงาน 8:
 - ตัวช่วยสร้างครั้งเดียวตอน worker เริ่ม (worker/deps.py) แล้วอยู่ใน ctx ของ arq ไม่สร้างใหม่ทุกงาน
 - claim (pending -> processing แบบมีเงื่อนไข) คือด่านกันรันซ้ำ: ได้ None = มีคนทำแล้ว (fast path / งานอื่น) ข้าม
 - พังระหว่าง run_turn: attempts < 3 -> release (กลับเป็น pending) แล้ว Retry งานนี้ (ไม่ทำ event ใหม่กว่า
   ก่อนตัวที่พัง เพื่อรักษาลำดับ); attempts ครบ 3 -> failed แล้วทำตัวถัดไป
 - เพดานเวลาของงาน (job_timeout) ใช้ได้ราวหนึ่งรอบ AI: ทำต่อเฉพาะเมื่อเวลาที่เหลือพอสำหรับอีกหนึ่ง
   TURN_DEADLINE_S ไม่งั้นหยุดไว้ให้งานของ event นั้นเอง (หรือ cron) มาทำต่อ
 - ไม่ถือ transaction DB ข้าม run_turn: repository ทุกเมธอดเปิด-ปิด transaction เอง
 - job id = raw:{raw_event_id} และ keep_result=0 (ดู enqueue_event)

แนวคิดจาก: src/jobs/softnixWorker.js, src/pipeline/userQueue.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from arq import Retry

from app.logging import bind_ids, clear_ids
from app.pipeline import orchestrator
from app.pipeline.ports import Deps
from app.pipeline.schemas import ProcessStatus, RawEvent
from app.privacy.redact import redact_text
from app.worker.locks import UserLock, lock_ttl_ms

if TYPE_CHECKING:
    from app.pipeline.orchestrator import PipelineConfig

log = structlog.get_logger(__name__)

# ชื่อฟังก์ชันงานใน arq (= __qualname__ ของ process_event)
PROCESS_EVENT_JOB = "process_event"
JOB_ID_PREFIX = "raw:"

# event หนึ่งลองได้กี่ครั้ง (นับจาก attempts ที่ claim เพิ่มให้) ครบแล้ว -> failed
MAX_EVENT_ATTEMPTS = 3
# รอล็อกของ user ไม่เกินเท่านี้ (วินาที) ไม่ได้ -> Retry หลัง LOCK_RETRY_DEFER_S
LOCK_WAIT_S = 3.0
LOCK_RETRY_DEFER_S = 5.0
# ลองใหม่หลังพังชั่วคราว (วินาที)
FAILURE_RETRY_DEFER_S = 10.0
# job_timeout = TURN_DEADLINE_S + เท่านี้ (settings.py ใช้ค่านี้)
JOB_TIMEOUT_MARGIN_S = 60
# ช่องเผื่อเวลาท้ายงานสำหรับ mark_done / ปล่อยล็อก
BATCH_TAIL_MARGIN_S = 10
# เริ่ม event ถัดไปในงานเดียวกันได้เมื่อใช้เวลาไปไม่เกินนี้: เวลาที่ใช้ + TURN_DEADLINE_S + ช่องท้าย <= job_timeout
BATCH_BUDGET_S = JOB_TIMEOUT_MARGIN_S - BATCH_TAIL_MARGIN_S
ERROR_MAX_CHARS = 500


class ArqPool(Protocol):
    """ส่วนของ ArqRedis ที่ใช้ใส่คิว"""

    async def enqueue_job(self, function: str, *args: Any, _job_id: str | None = None, **kwargs: Any) -> Any: ...


def job_id_for(raw_event_id: int) -> str:
    return f"{JOB_ID_PREFIX}{raw_event_id}"


async def enqueue_event(arq_pool: ArqPool, raw_event_id: int) -> bool:
    """ใส่คิว process_event ด้วย job id = raw:{raw_event_id}; คืน False ถ้ามีงาน id นี้อยู่แล้ว

    arq ไม่รับ job id ซ้ำตราบที่ key ของงาน (รอ/กำลังทำ/รอ retry) หรือ key ผลลัพธ์ยังอยู่
    WorkerSettings จึงตั้ง keep_result=0: งานจบแล้วไม่เก็บผล -> cron (requeue_pending / release_stuck)
    ใส่คิว id เดิมใหม่ได้ทันที; ถ้าเก็บผล (ค่าตั้งต้น 1 ชม.) event ที่ถูก release กลับเป็น pending
    จะใส่คิวไม่ได้จนผลหมดอายุ การกันทำซ้ำจริงอยู่ที่ claim ใน DB ไม่ใช่ที่คิว
    """
    job = await arq_pool.enqueue_job(PROCESS_EVENT_JOB, raw_event_id, _job_id=job_id_for(raw_event_id))
    return job is not None


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
    """งาน arq: ทำ event นี้และ event ที่ค้างของ user เดียวกันตามลำดับ (ใต้ล็อกต่อ user)

    ctx ต้องมี deps (Deps), pipeline_cfg (PipelineConfig), user_lock (UserLock) — ตั้งใน settings.on_startup
    """
    deps: Deps = ctx["deps"]
    cfg: PipelineConfig = ctx["pipeline_cfg"]
    user_lock: UserLock = ctx["user_lock"]
    clear_ids()
    bind_ids(raw_event_id=raw_event_id)
    try:
        raw = await deps.raw_events.get(raw_event_id)
        if raw is None:
            log.warning("event_missing")
            return

        if raw.line_user_id is None:
            # ไม่มีเจ้าของ (เช่น event จากกลุ่ม) -> ไม่ต้องเรียงลำดับต่อ user ไม่ต้องล็อก
            claimed = await deps.raw_events.claim(raw_event_id)
            if claimed is None:
                log.info("event_claim_skipped")
                return
            await run_claimed(deps, claimed, cfg)
            return

        # ไม่ข้ามแม้ event ของงานนี้จะเสร็จแล้ว: Retry ของงานนี้อาจมาเพื่อ event อื่นของ user ที่ถูก release
        settings = deps.settings
        held = await user_lock.acquire(
            raw.line_user_id, lock_ttl_ms(settings.turn_deadline_s), LOCK_WAIT_S
        )
        if held is None:
            log.info("user_lock_busy", defer_s=LOCK_RETRY_DEFER_S)
            raise Retry(defer=LOCK_RETRY_DEFER_S)

        needs_retry = False
        async with held:
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
            raise Retry(defer=FAILURE_RETRY_DEFER_S)
    finally:
        clear_ids()


__all__ = [
    "JOB_ID_PREFIX",
    "JOB_TIMEOUT_MARGIN_S",
    "LOCK_RETRY_DEFER_S",
    "LOCK_WAIT_S",
    "MAX_EVENT_ATTEMPTS",
    "PROCESS_EVENT_JOB",
    "enqueue_event",
    "error_text",
    "job_id_for",
    "process_event",
    "run_claimed",
]

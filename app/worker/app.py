"""app/worker/app.py

คิวงาน procrastinate (คิวบน Postgres แทน arq+Redis — docs/decisions.md D-010): App, งาน, งาน periodic, ตัวรัน worker

รายละเอียด:
 - app = procrastinate.App บน PsycopgConnector (psycopg 3 async) — conninfo = DATABASE_URL อ่านตอนเปิด pool
   (ไม่ใช่ตอน import: เทสต์/smoke import ได้โดยไม่มี env) ใช้เมื่อเปิดผ่าน CLI procrastinate เช่น
   `procrastinate --app=app.worker.app.app healthchecks`
 - api และ worker เปิดคิวบน pool ของ build_deps ด้วย open_queue(pool) (ไม่มี connection pool ที่สอง)
 - งาน process_event(raw_event_id): ห่อ tasks.process_event + เพดานเวลา job_timeout (procrastinate ไม่มี timeout ในตัว)
   lock ต่อ user / queueing_lock ต่อ raw event ตั้งตอนใส่คิว (tasks.enqueue_event)
   ต้องลองใหม่ (EventRetry / error / หมดเวลา / ถูกตัดตอน shutdown) -> RetryStrategy รอ RETRY_WAIT_S ไม่เกิน MAX_JOB_ATTEMPTS
   ครบแล้วงาน failed แต่แถว line_raw_events ที่ยัง pending ให้ cron requeue_pending ใส่คิวใหม่
 - งาน periodic (แทน arq cron รอบเวลาเดิม; cron ของ procrastinate คิดเป็น UTC, คอลัมน์ที่ 6 = วินาที):
   requeue_pending ทุก 1 นาที (วินาที 5), release_stuck ทุก 2 นาที (วินาที 35), handoff_timeout ทุก 5 นาที (วินาที 15),
   retention วันละครั้ง 20:30 UTC (03:30 เวลาไทย), retry_stalled_jobs ทุก 1 นาที (วินาที 50),
   remove_old_jobs วันละครั้ง 21:00 UTC — แต่ละตัวมี queueing_lock (ไม่กองในคิว) + lock (ไม่รันซ้อนตัวเอง)
 - worker: run_worker(settings) = observability -> PipelineConfig (ไฟล์กฎผิด = หยุดก่อนต่อ DB) -> build_deps
   -> open_queue -> app.run_worker_async(...) (วิธีรันในโค้ดตามเอกสาร procrastinate howto/basics/worker) -> ปิดทุกอย่าง
   ของที่งานใช้ส่งทาง additional_context: deps, pipeline_cfg, raw_retention, reply_token_retention
   ใน compose: `python -m app.worker` (app/worker/__main__.py)
 - งานที่จบสำเร็จลบทิ้งทันที (delete_jobs=successful); failed/cancelled/aborted เก็บ OLD_JOBS_MAX_HOURS ไว้ดู แล้ว remove_old_jobs ลบ
 - worker ตายกลางทาง: heartbeat หยุด -> retry_stalled_jobs คืนงานเข้าคิว (งานที่ค้าง doing ถือ lock ของ user ไว้)

แนวคิดจาก: app/worker/settings.py เดิม (WorkerSettings ของ arq)
สถานะ: มีโค้ดแล้ว (D-010) — ยังไม่ได้ลองบน Docker จริง
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from procrastinate import App, JobContext, PsycopgConnector, RetryStrategy, builtin_tasks
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, get_settings
from app.pipeline import orchestrator
from app.worker import cron, tasks
from app.worker.deps import build_deps, close_resources, init_observability

log = structlog.get_logger(__name__)

EVENTS_QUEUE = "events"
MAINTENANCE_QUEUE = "maintenance"
WORKER_NAME = "worker"

MIN_CONCURRENCY = 4
# งานหนึ่งลองได้กี่ครั้ง (procrastinate นับ attempts) — event หนึ่งลองเองได้ MAX_EVENT_ATTEMPTS ใน tasks.py
MAX_JOB_ATTEMPTS = 10
# รอเท่านี้ (วินาที) ก่อนรันงานที่ต้องลองใหม่ (งานใหม่กว่าของ user เดียวกันรอไปด้วย เพราะ lock เรียงตามเวลาสร้าง)
RETRY_WAIT_S = 10
# ตอนหยุด worker (SIGTERM) รองานที่ทำอยู่ไม่เกินเท่านี้ แล้วตัด (งานที่ถูกตัดกลับเข้าคิว) — compose ตั้ง
# stop_grace_period ให้มากกว่านี้
SHUTDOWN_GRACEFUL_TIMEOUT_S = 20.0
# เพดานเวลาของงานบำรุงรักษา (วินาที)
MAINTENANCE_TIMEOUT_S = 300
RETENTION_TIMEOUT_S = 600
# งานที่จบไม่สำเร็จเก็บไว้ดูกี่ชั่วโมง (สำเร็จแล้วลบทันที) — ในงานมีแค่ raw_event_id + lock ที่เป็น hash
OLD_JOBS_MAX_HOURS = 7 * 24

EVENT_RETRY = RetryStrategy(max_attempts=MAX_JOB_ATTEMPTS, wait=RETRY_WAIT_S)


def _conninfo() -> str:
    """DATABASE_URL อ่านตอน pool ของ connector ตั้งต้นเปิด (เช่นผ่าน CLI procrastinate) — URL มีรหัสผ่าน ห้ามลง log"""
    return get_settings().database_url.get_secret_value()


# psycopg_pool >= 3.3 รับ conninfo เป็น callable (อ่าน env ตอนเปิด ไม่ใช่ตอน import)
app = App(connector=PsycopgConnector(conninfo=_conninfo))


def concurrency_for(settings: Settings) -> int:
    """งานพร้อมกันต่อ worker = max(AI_CONCURRENCY * 2, 4)

    Semaphore ใน ai_client คุมจำนวนที่เรียก AI จริงไว้แล้ว ช่องที่เกินมาให้งานที่ไม่ใช้ AI (กฎ/FAQ/ส่งต่อ)
    และงาน periodic ไม่ต้องรอคิวหลังงานที่รอ AI; งานที่ติด lock ของ user ไม่กินช่อง (procrastinate ไม่หยิบ)
    — ค่าจริงรอคำตอบเรื่อง AI API
    """
    return max(settings.ai_concurrency * 2, MIN_CONCURRENCY)


def job_timeout_for(settings: Settings) -> float:
    """เพดานเวลาของงาน process_event = TURN_DEADLINE_S + 60 วิ (มากกว่าเพดานของ call_ai เสมอ)"""
    return settings.turn_deadline_s + tasks.JOB_TIMEOUT_MARGIN_S


def _ctx(context: JobContext) -> dict[str, Any]:
    return context.additional_context


# --- งานหลัก ---


@app.task(
    name=tasks.PROCESS_EVENT_TASK, queue=EVENTS_QUEUE, pass_context=True, retry=EVENT_RETRY
)
async def process_event_job(context: JobContext, raw_event_id: int) -> None:
    """งานของ procrastinate: tasks.process_event ภายใต้เพดานเวลา (หมดเวลา -> TimeoutError -> retry)"""
    ctx = _ctx(context)
    async with asyncio.timeout(job_timeout_for(ctx["deps"].settings)):
        await tasks.process_event(ctx, raw_event_id)


# --- งาน periodic (ฟังก์ชันจริงอยู่ใน cron.py) ---


def _maintenance(name: str) -> dict[str, Any]:
    """ค่าของงานบำรุงรักษา: queueing_lock กันกองในคิว, lock กันรันซ้อนตัวเอง"""
    return {
        "name": name,
        "queue": MAINTENANCE_QUEUE,
        "queueing_lock": name,
        "lock": f"maintenance:{name}",
        "pass_context": True,
    }


@app.periodic(cron="* * * * * 5")
@app.task(**_maintenance("requeue_pending"))
async def requeue_pending_job(context: JobContext, timestamp: int) -> int:
    async with asyncio.timeout(MAINTENANCE_TIMEOUT_S):
        return await cron.requeue_pending(_ctx(context)["deps"], context.app)


@app.periodic(cron="*/2 * * * * 35")
@app.task(**_maintenance("release_stuck"))
async def release_stuck_job(context: JobContext, timestamp: int) -> int:
    async with asyncio.timeout(MAINTENANCE_TIMEOUT_S):
        return await cron.release_stuck(_ctx(context)["deps"], context.app)


@app.periodic(cron="*/5 * * * * 15")
@app.task(**_maintenance("handoff_timeout"))
async def handoff_timeout_job(context: JobContext, timestamp: int) -> int:
    async with asyncio.timeout(MAINTENANCE_TIMEOUT_S):
        return await cron.handoff_timeout(_ctx(context)["deps"])


@app.periodic(cron="30 20 * * *")
@app.task(**_maintenance("retention"))
async def retention_job(context: JobContext, timestamp: int) -> None:
    ctx = _ctx(context)
    async with asyncio.timeout(RETENTION_TIMEOUT_S):
        await cron.retention(ctx["deps"], ctx["raw_retention"], ctx["reply_token_retention"])


@app.periodic(cron="* * * * * 50")
@app.task(**_maintenance("retry_stalled_jobs"))
async def retry_stalled_jobs_job(context: JobContext, timestamp: int) -> int:
    async with asyncio.timeout(MAINTENANCE_TIMEOUT_S):
        return await cron.retry_stalled_jobs(context.app.job_manager)


@app.periodic(cron="0 21 * * *")
@app.task(**_maintenance("remove_old_jobs"))
async def remove_old_jobs_job(context: JobContext, timestamp: int) -> None:
    """ตามเอกสาร procrastinate howto/production/delete_finished_jobs"""
    async with asyncio.timeout(MAINTENANCE_TIMEOUT_S):
        await builtin_tasks.remove_old_jobs(
            context,
            max_hours=OLD_JOBS_MAX_HOURS,
            remove_failed=True,
            remove_cancelled=True,
            remove_aborted=True,
        )


# --- เปิดคิว / รัน worker ---


@asynccontextmanager
async def open_queue(pool: AsyncConnectionPool) -> AsyncIterator[App]:
    """เปิดคิวบน pool ที่เปิดแล้วของเรา (api และ worker) — ออกจาก block แล้วคืน connector ตั้งต้น

    pool ตั้งจากข้างนอก procrastinate จึงไม่ปิด pool เอง (close_resources ปิด)
    """
    connector = PsycopgConnector()
    await connector.open_async(pool)
    try:
        with app.replace_connector(connector):
            yield app
    finally:
        await connector.close_async()


def worker_options(settings: Settings, context: dict[str, Any]) -> dict[str, Any]:
    """argument ของ App.run_worker_async"""
    return {
        "name": WORKER_NAME,
        "concurrency": concurrency_for(settings),
        "additional_context": context,
        "delete_jobs": "successful",
        "shutdown_graceful_timeout": SHUTDOWN_GRACEFUL_TIMEOUT_S,
    }


async def run_worker(settings: Settings) -> None:
    """เริ่ม worker จนกว่าจะได้ SIGTERM/SIGINT (procrastinate ตั้ง signal handler เอง) แล้วปิดทุกอย่าง"""
    init_observability(settings)
    # โหลดไฟล์กฎก่อนเปิด connection: ไฟล์ผิด = หยุดทันทีไม่ต้องปิดอะไร
    cfg = orchestrator.load_pipeline_config(settings)
    deps, res = await build_deps(settings)
    try:
        async with open_queue(res.pool) as queue:
            context: dict[str, Any] = {
                "deps": deps,
                "pipeline_cfg": cfg,
                "raw_retention": res.repos.raw_events,
                "reply_token_retention": res.reply_tokens,
            }
            log.info("worker_started", concurrency=concurrency_for(settings))
            await queue.run_worker_async(**worker_options(settings, context))
    finally:
        await close_resources(res)
        log.info("worker_stopped")


__all__ = [
    "EVENTS_QUEUE",
    "EVENT_RETRY",
    "MAINTENANCE_QUEUE",
    "MAX_JOB_ATTEMPTS",
    "app",
    "concurrency_for",
    "job_timeout_for",
    "open_queue",
    "process_event_job",
    "run_worker",
    "worker_options",
]

"""app/worker/settings.py

WorkerSettings ของ arq: ฟังก์ชันงาน, cron, การเชื่อมต่อ Redis

รายละเอียด:
 - functions = [tasks.process_event]
 - cron_jobs = [requeue_pending, handoff_timeout, retention] (ดู cron.py)
 - on_startup / on_shutdown: เปิด-ปิด pool ของ Postgres, httpx client, MinIO
 - max_jobs สัมพันธ์กับ AI_CONCURRENCY (ค่าจริงรอคำตอบเรื่อง AI API)
 - job_timeout > TURN_DEADLINE_S
 - Redis เป็นคิวอย่างเดียว

ข้อตัดสินในงาน 8:
 - WorkerSettings สร้างตอนถูกอ้างถึงครั้งแรก (module __getattr__) เพราะต้องอ่าน env: `arq app.worker.settings.WorkerSettings`
   ได้ค่าจริง (ขาดค่าบังคับ = หยุดทันที) ส่วนเทสต์เรียก make_worker_settings(settings) ได้โดยไม่ต้องมี env
 - cron: requeue_pending ทุก 1 นาที, release_stuck ทุก 2 นาที, handoff_timeout ทุก 5 นาที, retention วันละครั้ง
   03:30 เวลาไทย (20:30 UTC — cron ของ arq ใช้เวลา UTC ของเครื่อง container)
 - keep_result = 0 (ดู tasks.enqueue_event: ให้ cron ใส่คิว job id เดิมใหม่ได้)

แนวคิดจาก: src/jobs/softnixWorker.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

from typing import Any

import structlog
from arq.cron import cron

from app.config import Settings, get_settings
from app.pipeline import orchestrator
from app.worker import cron as cron_jobs
from app.worker.deps import build_deps, close_resources, init_observability, redis_settings_from
from app.worker.locks import UserLock
from app.worker.tasks import JOB_TIMEOUT_MARGIN_S, process_event

log = structlog.get_logger(__name__)

MIN_MAX_JOBS = 4
# arq ลองงานซ้ำ (Retry) ได้กี่ครั้ง: รวมรอบที่รอล็อกไม่ได้ — หมดแล้วแถวยังเป็น pending ให้ cron เก็บกวาด
MAX_TRIES = 10
RETENTION_TIMEOUT_S = 600


def max_jobs_for(settings: Settings) -> int:
    """งานพร้อมกันต่อ worker = max(AI_CONCURRENCY * 2, 4)

    Semaphore ใน ai_client คุมจำนวนที่เรียก AI จริงไว้แล้ว ช่องที่เกินมาให้งานที่ไม่ใช้ AI (กฎ/FAQ/ส่งต่อ)
    และงานที่รอล็อกของ user อื่นไม่ต้องรอคิวหลังงานที่รอ AI — ค่าจริงรอคำตอบเรื่อง AI API
    """
    return max(settings.ai_concurrency * 2, MIN_MAX_JOBS)


def job_timeout_for(settings: Settings) -> int:
    """job_timeout = TURN_DEADLINE_S + 60 วิ (มากกว่าเพดานของ call_ai เสมอ)"""
    return int(settings.turn_deadline_s + JOB_TIMEOUT_MARGIN_S)


def make_worker_settings(settings: Settings) -> type:
    """สร้างคลาส WorkerSettings ของ arq จาก Settings"""

    async def on_startup(ctx: dict[str, Any]) -> None:
        init_observability(settings)
        # โหลดไฟล์กฎก่อนเปิด connection: ไฟล์ผิด = หยุดทันทีไม่ต้องปิดอะไร
        cfg = orchestrator.load_pipeline_config(settings)
        deps, res = await build_deps(settings)
        ctx["settings"] = settings
        ctx["pipeline_cfg"] = cfg
        ctx["deps"] = deps
        ctx["resources"] = res
        ctx["user_lock"] = UserLock(res.redis)
        ctx["user_lookup"] = res.user_lookup
        ctx["raw_retention"] = res.repos.raw_events
        log.info("worker_started", max_jobs=max_jobs_for(settings))

    async def on_shutdown(ctx: dict[str, Any]) -> None:
        res = ctx.get("resources")
        if res is not None:
            await close_resources(res)
        log.info("worker_stopped")

    every_2_min = set(range(0, 60, 2))
    every_5_min = set(range(0, 60, 5))

    attrs: dict[str, Any] = {
        "functions": [process_event],
        "cron_jobs": [
            cron(cron_jobs.requeue_pending, name="requeue_pending", second=5),
            cron(cron_jobs.release_stuck, name="release_stuck", minute=every_2_min, second=35),
            cron(cron_jobs.handoff_timeout, name="handoff_timeout", minute=every_5_min, second=15),
            cron(
                cron_jobs.retention,
                name="retention",
                hour={20},
                minute={30},
                second=0,
                timeout=RETENTION_TIMEOUT_S,
            ),
        ],
        "on_startup": on_startup,
        "on_shutdown": on_shutdown,
        "redis_settings": redis_settings_from(settings),
        "max_jobs": max_jobs_for(settings),
        "job_timeout": job_timeout_for(settings),
        "keep_result": 0,
        "max_tries": MAX_TRIES,
    }
    return type("WorkerSettings", (), attrs)


_cached: type | None = None


def __getattr__(name: str) -> Any:
    # `arq app.worker.settings.WorkerSettings` -> getattr(module, "WorkerSettings") มาที่นี่ (อ่าน env ครั้งแรก)
    global _cached
    if name == "WorkerSettings":
        if _cached is None:
            _cached = make_worker_settings(get_settings())
        return _cached
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MAX_TRIES", "job_timeout_for", "make_worker_settings", "max_jobs_for"]

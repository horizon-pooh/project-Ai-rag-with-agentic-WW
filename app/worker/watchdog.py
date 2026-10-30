"""app/worker/watchdog.py

`python -m app.worker.watchdog` — เฝ้าคิวจากนอก worker แล้วแจ้งผู้ดูแลเมื่อระบบหยุดตอบลูกค้า (D-012)
รันเป็น service แยกใน docker-compose.yml (image เดียวกับ worker) เพราะ worker ที่ตายแล้วแจ้งเรื่องตัวเองไม่ได้
(retry_stalled_jobs ใน cron.py ต้องมี worker อย่างน้อยหนึ่งตัวยังรันอยู่)

รายละเอียด:
 - ทุก WATCHDOG_INTERVAL_S อ่าน repositories/health.queue_health แล้วหาปัญหา (detect):
   db_unreachable   ต่อ Postgres ไม่ได้ / ตอบช้าเกิน CHECK_TIMEOUT_S
   no_worker        ไม่มี worker ลงทะเบียนเลย (ปิดหมด หรือ container ไม่ขึ้น)
   worker_stale     มีแถว worker แต่ heartbeat ล่าสุดเก่ากว่า WATCHDOG_HEARTBEAT_STALE_S (ตายกะทันหันทั้งหมด)
   pending_stuck    raw event pending เก่ากว่า WATCHDOG_PENDING_STALE_S (คิวไม่เดิน แม้ worker ดูเหมือนยังอยู่)
 - AlertTracker: แจ้งเมื่อปัญหาอยู่ต่อเนื่องครบ WATCHDOG_GRACE_S (deploy/restart ปกติไม่แจ้ง),
   แจ้งซ้ำทุก WATCHDOG_REPEAT_S ถ้ายังไม่หาย, แจ้ง "หายแล้ว" เมื่อปัญหาที่เคยแจ้งหายไป
 - แจ้งผ่าน services/alert (log + Sentry [+ อีเมล]) — ข้อความไม่มีข้อมูลลูกค้า มีแค่จำนวนและอายุ
 - ต่อ DB ไม่ได้ตอนเริ่มก็ไม่ล้ม: ลองเปิด pool ใหม่ทุกรอบ และนับเป็น db_unreachable
 - ไม่เขียนอะไรลง DB, ไม่เรียก LINE, ไม่แตะคิว
 - Windows: psycopg async ต้องใช้ SelectorEventLoop (production เป็น Linux container)

แนวคิดจาก: ใหม่ (D-012 ข้อ "แจ้งเตือนเมื่อ worker ตาย", docs/Line_ai_bot.md หัวข้อเฝ้าระวัง)
สถานะ: มีโค้ดแล้ว — ยังไม่ได้ทดสอบกับ Postgres / SMTP จริง
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import structlog
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, get_settings
from app.repositories.db import close_pool, open_pool
from app.repositories.health import QueueHealth, queue_health
from app.services.alert import Alerter, make_alerter
from app.worker.deps import init_observability

log = structlog.get_logger(__name__)

DB_UNREACHABLE = "db_unreachable"
NO_WORKER = "no_worker"
WORKER_STALE = "worker_stale"
PENDING_STUCK = "pending_stuck"
# ปัญหาที่ต้องอ่าน DB ถึงรู้ — ตอน DB ล่มไม่รู้สถานะ ห้ามนับว่า "หายแล้ว"
NEEDS_DB = frozenset({NO_WORKER, WORKER_STALE, PENDING_STUCK})

# อ่านสุขภาพคิวหนึ่งครั้งรอไม่เกินเท่านี้ (วินาที) — เกิน = db_unreachable
CHECK_TIMEOUT_S = 15.0
# เปิด pool ใหม่รอไม่เกินเท่านี้ต่อรอบ (ไม่ใช่ 30 วิของ api/worker — watchdog ต้องรายงานทันรอบ)
POOL_OPEN_TIMEOUT_S = 10.0

# คำอธิบาย + สิ่งที่ควรทำ (ภาษาของผู้ดูแล ไม่มีข้อมูลลูกค้า)
_HINTS = {
    DB_UNREACHABLE: "ดู `docker compose ps postgres` และ `docker compose logs --tail=100 postgres`",
    NO_WORKER: "ดู `docker compose ps worker` และ `docker compose logs --tail=100 worker` แล้ว `docker compose up -d worker`",
    WORKER_STALE: "worker ค้างหรือตายกะทันหัน: `docker compose restart worker` แล้วดู log",
    PENDING_STUCK: "ข้อความลูกค้าค้างคิว: ดู log ของ worker; ถ้าแก้ไม่ทันเปิด kill switch ให้ call center รับแทน",
}


def detect(health: QueueHealth | None, settings: Settings) -> dict[str, str]:
    """สุขภาพคิว -> {รหัสปัญหา: คำอธิบาย}; None = ต่อ DB ไม่ได้ (ตรวจอย่างอื่นไม่ได้)"""
    if health is None:
        return {DB_UNREACHABLE: "ต่อ Postgres ไม่ได้"}
    problems: dict[str, str] = {}
    if health.workers == 0:
        problems[NO_WORKER] = "ไม่มี worker ทำงานอยู่เลย — ข้อความลูกค้าไม่มีใครประมวลผล"
    elif (
        health.heartbeat_age_s is not None
        and health.heartbeat_age_s > settings.watchdog_heartbeat_stale_s
    ):
        problems[WORKER_STALE] = (
            f"worker {health.workers} ตัวไม่ส่ง heartbeat มา {health.heartbeat_age_s:.0f} วิ"
        )
    if (
        health.oldest_pending_age_s is not None
        and health.oldest_pending_age_s > settings.watchdog_pending_stale_s
    ):
        problems[PENDING_STUCK] = (
            f"มีข้อความลูกค้าค้างคิว (pending) นาน {health.oldest_pending_age_s / 60:.0f} นาที"
        )
    return problems


@dataclass(frozen=True)
class Notice:
    kind: Literal["problem", "recovered"]
    code: str
    text: str


@dataclass
class AlertTracker:
    """จำว่าปัญหาไหนเริ่มเมื่อไหร่ / แจ้งไปเมื่อไหร่ — ไม่มี I/O (เวลาเป็นวินาทีจาก monotonic)"""

    grace_s: float
    repeat_s: float
    first_seen: dict[str, float] = field(default_factory=dict)
    alerted_at: dict[str, float] = field(default_factory=dict)

    def observe(
        self, now: float, problems: dict[str, str], unknown: frozenset[str] = frozenset()
    ) -> list[Notice]:
        """unknown = รหัสที่รอบนี้ตรวจไม่ได้: คงสถานะเดิมไว้ (ไม่แจ้งหาย ไม่นับเวลาใหม่)"""
        notices: list[Notice] = []
        for code, text in problems.items():
            started = self.first_seen.setdefault(code, now)
            if now - started < self.grace_s:
                continue
            last = self.alerted_at.get(code)
            if last is None or now - last >= self.repeat_s:
                self.alerted_at[code] = now
                notices.append(Notice("problem", code, text))
        for code in list(self.first_seen):
            if code in problems or code in unknown:
                continue
            del self.first_seen[code]
            if self.alerted_at.pop(code, None) is not None:
                notices.append(Notice("recovered", code, "กลับมาปกติแล้ว"))
        return notices


def format_notice(notice: Notice, at: datetime) -> tuple[str, str]:
    """Notice -> (หัวเรื่อง, เนื้อความ) สำหรับ Alerter"""
    stamp = at.strftime("%Y-%m-%d %H:%M:%S %Z")
    if notice.kind == "recovered":
        return f"หายแล้ว: {notice.code}", f"{notice.code} {notice.text} ({stamp})"
    body = f"{notice.text}\nเวลา: {stamp}\nสิ่งที่ควรทำ: {_HINTS.get(notice.code, '-')}"
    return f"ระบบตอบแชทมีปัญหา: {notice.code}", body


class QueueProbe:
    """ถือ pool ของ watchdog (ขนาดเล็ก) — เปิดใหม่เองถ้ายังไม่มี; ทุก error = None (ต่อ DB ไม่ได้)"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: AsyncConnectionPool | None = None

    async def __call__(self) -> QueueHealth | None:
        try:
            if self._pool is None:
                self._pool = await open_pool(
                    self._settings.database_url, max_size=2, timeout_s=POOL_OPEN_TIMEOUT_S
                )
            return await asyncio.wait_for(queue_health(self._pool), CHECK_TIMEOUT_S)
        except Exception as e:
            log.warning("watchdog_check_failed", error_type=type(e).__name__)
            return None

    async def close(self) -> None:
        if self._pool is not None:
            await close_pool(self._pool)
            self._pool = None


async def run_watchdog(
    settings: Settings,
    *,
    probe: Callable[[], Awaitable[QueueHealth | None]],
    alerter: Alerter,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_rounds: int | None = None,
) -> None:
    """วนตรวจจนถูกยกเลิก (max_rounds ไว้ให้เทสต์หยุดได้)"""
    tracker = AlertTracker(grace_s=settings.watchdog_grace_s, repeat_s=settings.watchdog_repeat_s)
    rounds = 0
    while max_rounds is None or rounds < max_rounds:
        rounds += 1
        health = await probe()
        problems = detect(health, settings)
        unknown = NEEDS_DB if health is None else frozenset()
        for notice in tracker.observe(monotonic(), problems, unknown):
            subject, body = format_notice(notice, datetime.now(settings.tz))
            await alerter.send(subject, body)
        if problems:
            log.warning("watchdog_problems", codes=sorted(problems))
        await sleep(settings.watchdog_interval_s)


async def _main(settings: Settings) -> None:
    alerter = make_alerter(settings)
    probe = QueueProbe(settings)
    log.info(
        "watchdog_started",
        channel=(settings.alert_channel or "log"),
        interval_s=settings.watchdog_interval_s,
        grace_s=settings.watchdog_grace_s,
    )
    try:
        await run_watchdog(settings, probe=probe, alerter=alerter)
    finally:
        await probe.close()


def main() -> None:
    settings = get_settings()
    init_observability(settings)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_main(settings), loop_factory=loop_factory)


if __name__ == "__main__":
    main()

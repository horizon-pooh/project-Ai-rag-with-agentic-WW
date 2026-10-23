"""app/worker/deps.py

ประกอบตัวช่วยทั้งหมด (Deps) จาก Settings ครั้งเดียวตอนเริ่ม process — ใช้ร่วมกันทั้ง worker และ api

รายละเอียด:
 - build_deps(settings) -> (Deps, Resources): pool Postgres, repositories, httpx client (ใช้ร่วมทั้ง process),
   Redis (ArqRedis จาก arq.create_pool: ใช้ทั้งใส่คิว ล็อก และ replyToken), MinIO, ai/line/notifier/kill switch,
   reply tokens, SystemClock
 - Resources = ของที่ต้องปิดตอน shutdown + ของที่ไม่อยู่ใน Deps (repos ตัวจริง)
 - close_resources(res): ปิดทุกอย่าง (พังตัวหนึ่งไม่หยุดตัวอื่น)
 - เปิดพลางกลางทาง (เช่น Redis ต่อไม่ได้) -> ปิดของที่เปิดไปแล้วก่อนยก error (fail fast ไม่รั่ว)
 - init_observability(settings): ตั้ง logging + Sentry (เฉพาะเมื่อมี SENTRY_DSN, ไม่ส่ง PII)
 - check_ready(res): Postgres SELECT 1 + Redis PING สำหรับ /health/ready

แนวคิดจาก: ใหม่ (งาน 8)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from arq.connections import ArqRedis, RedisSettings, create_pool
from psycopg_pool import AsyncConnectionPool

from app.config import Settings
from app.logging import configure_logging
from app.pipeline.ports import AiClient, Deps, SystemClock
from app.repositories import Repos, close_pool, make_repos, open_pool
from app.repositories.health import ping as ping_db
from app.services.ai_client import HttpAiClient, make_ai_client
from app.services.kill_switch import FlagsKillSwitch
from app.services.line_client import HttpLineClient
from app.services.notify import make_notifier
from app.services.redis_store import RedisReplyTokens
from app.services.storage import MinioStorage

log = structlog.get_logger(__name__)

# readiness: แต่ละการตรวจรอไม่เกินเท่านี้ (วินาที)
READY_CHECK_TIMEOUT_S = 2.0


@dataclass(kw_only=True)
class Resources:
    """ของที่ build_deps เปิดไว้ (ต้องปิดตอน shutdown) และของที่ไม่อยู่ใน Deps"""

    pool: AsyncConnectionPool
    repos: Repos
    redis: ArqRedis
    line_http: httpx.AsyncClient
    ai_http: httpx.AsyncClient
    ai: AiClient


def redis_settings_from(settings: Settings) -> RedisSettings:
    """RedisSettings ของ arq จาก REDIS_URL (URL อาจมีรหัสผ่าน ห้ามลง log)"""
    return RedisSettings.from_dsn(settings.redis_url.get_secret_value())


def init_observability(settings: Settings) -> None:
    """ตั้ง structlog ตาม LOG_LEVEL และต่อ Sentry เฉพาะเมื่อมี SENTRY_DSN"""
    configure_logging(settings.log_level)
    if settings.sentry_dsn is None or not settings.sentry_dsn.get_secret_value():
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        # ไม่ส่ง header / IP / body ของ request (มี signature, token ลิงก์ /close)
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    log.info("sentry_enabled")


async def build_deps(settings: Settings) -> tuple[Deps, Resources]:
    """เปิดทุกอย่างแล้วประกอบ Deps — ต่อ Postgres/Redis ไม่ได้ = ยก error (fail fast)"""
    opened: list[Any] = []  # (ชนิด, ของ) ที่เปิดแล้ว ไว้ปิดถ้าพังกลางทาง
    try:
        pool = await open_pool(settings.database_url)
        opened.append(("pool", pool))
        redis = await create_pool(redis_settings_from(settings))
        opened.append(("redis", redis))
        line_http = httpx.AsyncClient()
        opened.append(("http", line_http))
        ai_http = httpx.AsyncClient()
        opened.append(("http", ai_http))

        repos = make_repos(pool)
        # AI_API_URL ตั้งแล้ว -> HttpAiClient บน httpx client ที่เราปิดเอง; ว่าง -> stub (make_ai_client log เตือน)
        ai: AiClient = (
            HttpAiClient(settings, http=ai_http) if settings.ai_enabled else make_ai_client(settings)
        )
        deps = Deps(
            settings=settings,
            clock=SystemClock(),
            raw_events=repos.raw_events,
            users=repos.users,
            conversations=repos.conversations,
            state=repos.state,
            messages=repos.messages,
            ai_runs=repos.ai_runs,
            feedback=repos.feedback,
            flags=repos.flags,
            ai=ai,
            line=HttpLineClient(settings, http=line_http),
            reply_tokens=RedisReplyTokens(redis, settings.reply_token_ttl_s),
            notifier=make_notifier(settings),
            # MinIO สร้าง bucket ตอนใช้ครั้งแรก (MinIO ยังไม่พร้อมก็เริ่มได้)
            storage=MinioStorage(settings),
            kill_switch=FlagsKillSwitch(settings, repos.flags),
        )
        res = Resources(
            pool=pool,
            repos=repos,
            redis=redis,
            line_http=line_http,
            ai_http=ai_http,
            ai=ai,
        )
    except BaseException:
        await _close_opened(opened)
        raise
    log.info("deps_built", ai_enabled=settings.ai_enabled)
    return deps, res


async def _close_opened(opened: list[Any]) -> None:
    for kind, obj in reversed(opened):
        try:
            if kind == "pool":
                await close_pool(obj)
            else:
                await obj.aclose()
        except Exception:
            log.exception("resource_close_failed", kind=kind)


async def close_resources(res: Resources) -> None:
    """ปิดทุกอย่างที่ build_deps เปิด (ย้อนลำดับ) — ตัวหนึ่งพังไม่หยุดตัวอื่น"""
    await _close_opened(
        [
            ("pool", res.pool),
            ("redis", res.redis),
            ("http", res.line_http),
            ("http", res.ai_http),
        ]
    )


async def check_ready(res: Resources) -> dict[str, bool]:
    """readiness: postgres (SELECT 1) และ redis (PING) — คืนผลต่อชิ้น ไม่ยก error ไม่คืนรายละเอียด error"""

    async def _db() -> bool:
        return await ping_db(res.pool)

    async def _redis() -> bool:
        return bool(await res.redis.ping())

    results: dict[str, bool] = {}
    for name, check in (("postgres", _db), ("redis", _redis)):
        try:
            results[name] = await asyncio.wait_for(check(), READY_CHECK_TIMEOUT_S)
        except Exception:
            log.warning("ready_check_failed", check=name)
            results[name] = False
    return results


__all__ = [
    "READY_CHECK_TIMEOUT_S",
    "Resources",
    "build_deps",
    "check_ready",
    "close_resources",
    "init_observability",
    "redis_settings_from",
]

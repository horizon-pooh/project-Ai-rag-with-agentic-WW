"""app/main.py

สร้าง FastAPI app (app factory) และต่อ router ทั้งหมด ไม่มี business logic

รายละเอียด:
 - create_app(): โหลด config ครั้งเดียว, ตั้ง logging, ต่อ Sentry (ถ้ามี SENTRY_DSN)
 - ต่อ router: api/webhook, api/health, api/close, api/fast_path
 - เปิด/ปิด connection pool ของ Postgres (และคิว procrastinate บน pool เดียวกัน) ตอน startup/shutdown
 - ห้ามมี dev routes ใน production
 - รันด้วย uvicorn (คำสั่งอยู่ใน docker-compose.yml)
 - รันบน Windows (เครื่อง dev): เพิ่ม `--loop asyncio:SelectorEventLoop` เพราะ uvicorn บน Windows ใช้
   ProactorEventLoop ซึ่ง psycopg async ใช้ไม่ได้ (Linux container ไม่ต้องใช้)

ข้อตัดสินในงาน 8:
 - api/fast_path ไม่มี route ของตัวเอง (webhook เรียกเป็น BackgroundTask)
 - ปิด /docs /redoc /openapi.json (ไม่มี dev routes)
 - lifespan: logging + Sentry -> PipelineConfig (ไฟล์กฎ) -> build_deps (Postgres, httpx, MinIO)
   -> worker/app.open_queue(pool) เปิดคิว procrastinate บน pool เดียวกัน (D-010: ไม่มี Redis; replyToken อยู่ตาราง reply_tokens)
 - app.state: settings, deps, resources, queue (procrastinate App ที่เปิดแล้ว), pipeline_cfg, ready_check
   (เทสต์ตั้งเองได้โดยไม่รัน lifespan)

แนวคิดจาก: src/app.js, src/server.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from app.api import close, health, webhook
from app.config import Settings, get_settings
from app.pipeline import orchestrator
from app.worker.app import open_queue
from app.worker.deps import build_deps, check_ready, close_resources, init_observability


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    init_observability(settings)
    # ไฟล์กฎผิด = หยุดก่อนเปิด connection
    cfg = orchestrator.load_pipeline_config(settings)
    deps, res = await build_deps(settings)
    try:
        async with open_queue(res.pool) as queue:
            app.state.pipeline_cfg = cfg
            app.state.deps = deps
            app.state.resources = res
            app.state.queue = queue
            app.state.ready_check = partial(check_ready, res)
            yield
    finally:
        await close_resources(res)


def create_app(settings: Settings | None = None) -> FastAPI:
    """app factory (uvicorn --factory เรียกโดยไม่ส่งอะไร -> อ่าน env ผ่าน get_settings)"""
    app = FastAPI(
        title="line-prod",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings if settings is not None else get_settings()
    app.include_router(webhook.router)
    app.include_router(health.router)
    app.include_router(close.router)
    return app


__all__ = ["create_app", "lifespan"]

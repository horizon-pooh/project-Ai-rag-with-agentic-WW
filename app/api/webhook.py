"""app/api/webhook.py

POST /webhook: รับ event จาก LINE บันทึกก่อน แล้วใส่คิว ตอบ 200 ทันที

รายละเอียด:
 - ตรวจ signature จาก body ดิบ (X-Line-Signature) ไม่ผ่าน -> 401
 - บันทึก line_raw_events ก่อนตอบ 200 เสมอ (ตัด replyToken ออกจากแถว)
 - กันซ้ำด้วย UNIQUE webhook_event_id (ซ้ำ -> ข้าม ตอบ 200)
 - ใส่คิว procrastinate (worker/tasks.enqueue_event): lock ต่อ user + queueing_lock = raw:{raw id} กันซ้ำ;
   replyToken เก็บในตาราง reply_tokens (อายุสั้น ใช้ครั้งเดียว) ไม่ลง line_raw_events
 - คำสั่งเปลี่ยนสถานะ (talk_to_staff, resume_ai, ขอคนชัดเจน) ส่งต่อให้ api/fast_path ด้วย
 - ใส่คิวพลาด -> แถวยังเป็น pending, cron requeue_pending เก็บกวาด

ข้อตัดสินในงาน 8:
 - ซ้ำ (insert คืน None) -> ไม่เก็บ replyToken ไม่ใส่คิว ไม่วิ่งทางด่วน (แถวเดิมมีงานอยู่แล้ว หรือ cron เก็บ)
 - บันทึก DB พัง -> ปล่อยเป็น 500 ให้ LINE ส่งซ้ำ (บันทึกก่อนตอบรับ; ตัวที่บันทึกไปแล้วกันซ้ำด้วย UNIQUE)
 - เก็บ replyToken / ใส่คิว พัง -> log อย่างเดียว ยังตอบ 200 (push แทน reply / cron เก็บกวาด)
 - event ไม่มี webhookEventId (กันซ้ำไม่ได้) -> ข้าม + log; JSON ผิดรูป -> 400
 - event ทางด่วนยังใส่คิวด้วยเสมอ: ใคร claim ก่อนคนนั้นทำ อีกฝั่งข้าม
 - ห้าม log body / replyToken / ข้อความลูกค้า

แนวคิดจาก: src/routes/webhook.js, src/middleware/lineSignature.js, src/pipeline/dedupe.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.api.fast_path import run_fast_path
from app.pipeline import orchestrator
from app.pipeline.orchestrator import PipelineConfig
from app.pipeline.ports import Deps
from app.pipeline.schemas import InboundEvent
from app.services.line_client import verify_signature
from app.worker.tasks import enqueue_event

log = structlog.get_logger(__name__)

router = APIRouter()

SIGNATURE_HEADER = "X-Line-Signature"


def _occurred_at(event: dict[str, Any], deps: Deps) -> datetime:
    ts = event.get("timestamp")
    if isinstance(ts, int | float) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts / 1000, tz=UTC)
    return deps.clock.now()


def _line_user_id(event: dict[str, Any]) -> str | None:
    source = event.get("source")
    if isinstance(source, dict):
        uid = source.get("userId")
        if isinstance(uid, str) and uid:
            return uid
    return None


def _is_fast_path(raw_event_id: int, payload: dict[str, Any], cfg: PipelineConfig) -> bool:
    try:
        inbound = InboundEvent.from_line_event(raw_event_id, payload)
    except ValueError:
        # ไม่มี userId / ข้อมูลไม่ครบ -> ไม่ใช่คำสั่งของลูกค้า
        return False
    try:
        return bool(orchestrator.is_fast_path_event(inbound, cfg))
    except Exception:
        log.exception("fast_path_check_failed", raw_event_id=raw_event_id)
        return False


@router.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks) -> Response:
    state = request.app.state
    deps: Deps = state.deps
    body = await request.body()

    # ตรวจเสมอ ไม่มีโหมดข้าม
    secret = deps.settings.line_channel_secret.get_secret_value()
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        log.warning("webhook_bad_signature")
        return Response(status_code=401)

    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        log.warning("webhook_bad_json")
        return Response(status_code=400)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        log.warning("webhook_no_events")
        return Response(status_code=400)

    for event in events:
        if not isinstance(event, dict):
            log.warning("webhook_event_not_object")
            continue
        webhook_event_id = event.get("webhookEventId")
        if not isinstance(webhook_event_id, str) or not webhook_event_id:
            log.warning("webhook_event_no_id", event_type=event.get("type"))
            continue

        reply_token = event.get("replyToken")
        payload = {k: v for k, v in event.items() if k != "replyToken"}
        event_type = event.get("type")
        raw_event_id = await deps.raw_events.insert(
            webhook_event_id=webhook_event_id,
            line_user_id=_line_user_id(event),
            event_type=event_type if isinstance(event_type, str) and event_type else "other",
            payload=payload,
            occurred_at=_occurred_at(event, deps),
        )
        if raw_event_id is None:
            log.info("webhook_duplicate", webhook_event_id=webhook_event_id)
            continue
        blog = log.bind(raw_event_id=raw_event_id)

        if isinstance(reply_token, str) and reply_token:
            try:
                await deps.reply_tokens.put(raw_event_id, reply_token)
            except Exception:
                blog.exception("reply_token_store_failed")

        if _is_fast_path(raw_event_id, payload, state.pipeline_cfg):
            background.add_task(run_fast_path, deps, raw_event_id, state.pipeline_cfg)
            blog.info("webhook_fast_path_scheduled")

        try:
            enqueued = await enqueue_event(state.queue, raw_event_id, _line_user_id(event))
            blog.info("webhook_enqueued", enqueued=enqueued)
        except Exception:
            # แถวยัง pending -> cron requeue_pending ใส่คิวให้
            blog.exception("webhook_enqueue_failed")

    return Response(status_code=200)


__all__ = ["router"]

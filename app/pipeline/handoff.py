"""app/pipeline/handoff.py

start_handoff / end_handoff: จุดเดียวที่เปลี่ยนสถานะ bot <-> human

รายละเอียด:
 - start_handoff(conv, reason): UPDATE ... SET status='human', handoff_started_at=now() WHERE status='bot'; สำเร็จ -> แจ้ง call center พร้อมลิงก์ /close
 - end_handoff(conv, reason): reason = user_resumed / timeout / staff_closed; ใช้ร่วม 3 ทาง: handoff_check, cron handoff_timeout, api/close
 - UPDATE แบบมีเงื่อนไข: UPDATE conversations SET status='bot', handoff_ended_at=now(), handoff_end_reason=$2 WHERE id=$1 AND status='human' AND handoff_started_at=$3 RETURNING *
 - ใครมาก่อนชนะ: ไม่ได้แถวคืน -> ไม่ทำอะไร (ไม่ส่งข้อความ/ไม่แจ้งพนักงานซ้ำ)
 - สำเร็จ -> ล้าง conversation_state, แจ้งพนักงานว่าลูกค้ากลับไปคุยกับ AI
 - SQL จริงอยู่ใน repositories/conversations.py ที่นี่เรียกผ่านตัวช่วย
 - in_business_hours(now, settings): อยู่ในเวลาทำการ (CALLCENTER_HOURS ตาม TIMEZONE) ไหม
   orchestrator ใช้เลือก HANDOFF_IN_HOURS / HANDOFF_OUT_OF_HOURS
 - ที่นี่ไม่ส่งข้อความถึงลูกค้า (orchestrator ส่งผ่าน steps/send.py)

แนวคิดจาก: src/conversation/escalate.js, src/conversation/stateMachine.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 4)
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import structlog

from app.config import Settings
from app.pipeline.ports import Deps
from app.pipeline.schemas import Conversation, HandoffEndReason

log = structlog.get_logger(__name__)


def close_url_for(settings: Settings, token: str) -> str | None:
    """ลิงก์ /close ใช้ครั้งเดียว; ไม่ได้ตั้ง PUBLIC_BASE_URL -> None"""
    if not settings.public_base_url:
        return None
    return f"{settings.public_base_url}/close?token={quote(token, safe='')}"


async def start_handoff(
    deps: Deps, conv: Conversation, reason: str, *, summary: str
) -> Conversation | None:
    """bot -> human แล้วแจ้ง call center ครั้งเดียว

    คืนแถวใหม่ (status=human); None = มีทางอื่นส่งต่อไปก่อนแล้ว (เช่น fast path) -> ไม่แจ้งซ้ำ
    summary ต้อง redact แล้ว (ส่งออกนอกระบบ)
    """
    new = await deps.conversations.start_handoff(conv.id, reason)
    if new is None:
        log.info("handoff_start_skipped", conversation_id=conv.id, reason=reason)
        return None
    log.info("handoff_started", conversation_id=new.id, reason=reason)

    # ตั้งแต่นี้สถานะเป็น human แล้วใน DB: สร้างลิงก์/แจ้งพนักงานพังต้องไม่ย้อนการส่งต่อ
    # (ย้อนแล้วบอทจะตอบลูกค้าต่อทั้งที่ลูกค้าขอคน) -> log แล้วไปต่อ พนักงานยังเห็นเคสจาก log/Sentry
    close_url: str | None = None
    if deps.settings.public_base_url and new.handoff_started_at is not None:
        # ไม่มี PUBLIC_BASE_URL ก็ไม่สร้าง token (ไม่มีลิงก์ให้ใช้)
        try:
            token = await deps.conversations.create_close_token(
                new.id, new.handoff_started_at, deps.settings.close_token_ttl
            )
            close_url = close_url_for(deps.settings, token)
        except Exception:
            log.exception("close_token_create_failed", conversation_id=new.id)
    try:
        await deps.notifier.handoff_started(new, summary, close_url)
    except Exception:
        log.exception("notify_handoff_started_failed", conversation_id=new.id)
    return new


async def end_handoff(
    deps: Deps, conv: Conversation, reason: HandoffEndReason
) -> Conversation | None:
    """human -> bot (repo ล้าง conversation_state ใน transaction เดียวกัน) แล้วแจ้งพนักงานครั้งเดียว

    expected_started_at = conv.handoff_started_at ที่ผู้เรียกเห็น (กันปิดรอบใหม่ด้วยคำสั่งของรอบเก่า)
    None = ทางอื่นปิดไปก่อน / รอบไม่ตรง / conv ไม่ใช่ human -> ผู้เรียกไม่ส่งข้อความ ไม่แจ้งซ้ำ
    """
    if conv.handoff_started_at is None:
        # ไม่เคยส่งต่อ (ไม่มีรอบให้ปิด) — UPDATE แบบมีเงื่อนไขย่อมได้ 0 แถวอยู่แล้ว
        return None
    new = await deps.conversations.end_handoff(conv.id, reason, conv.handoff_started_at)
    if new is None:
        log.info("handoff_end_skipped", conversation_id=conv.id, reason=str(reason))
        return None
    log.info("handoff_ended", conversation_id=new.id, reason=str(reason))
    # แจ้งไม่สำเร็จต้องไม่ย้อนการคืน AI (สถานะใน DB เปลี่ยนแล้ว) -> log แล้วไปต่อ
    try:
        await deps.notifier.handoff_ended(new, reason)
    except Exception:
        log.exception("notify_handoff_ended_failed", conversation_id=new.id)
    return new


def in_business_hours(now: datetime, settings: Settings) -> bool:
    """อยู่ในเวลาทำการของ call center ไหม (ตาม settings.tz)

    - ไม่ได้ตั้ง CALLCENTER_HOURS -> True (ไม่รู้เวลาทำการ ใช้ข้อความในเวลาทำการ)
    - ช่วงปกติ เช่น 09:00-18:00: เริ่ม <= เวลา < เลิก
    - ช่วงข้ามเที่ยงคืน เช่น 22:00-06:00: เวลา >= เริ่ม หรือ เวลา < เลิก
    - เริ่ม == เลิก -> ถือว่าเปิดทั้งวัน
    - now ไม่มี timezone -> ถือเป็น UTC (Clock ควรคืน UTC อยู่แล้ว)
    """
    hours = settings.callcenter_hours_range
    if hours is None:
        return True
    start, end = hours
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    t = now.astimezone(settings.tz).time().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= t < end
    return t >= start or t < end

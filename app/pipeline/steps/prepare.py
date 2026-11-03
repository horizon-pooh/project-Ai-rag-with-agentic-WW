"""app/pipeline/steps/prepare.py

ขั้น 3: เตรียมข้อมูลก่อนตัดสินอะไร

รายละเอียด:
 - หา/สร้าง line_users และ conversation (bot เงียบเกิน 2 ชม. -> conversation ใหม่; human ห้ามเปิดใหม่)
 - บันทึกข้อความขาเข้า (messages)
 - รูป -> ดึงจาก LINE เก็บ MinIO; ระหว่าง human เก็บแต่ไม่ส่ง AI
 - เช็ค consent (line_consents)
 - สร้าง ai_runs
 - อ่าน conversation_state ที่ยังไม่หมดอายุ (หมดแล้ว -> state_expired)
 - ข้อความระหว่าง human ไม่ต่ออายุ state ไม่เลื่อน 6 ชม.

แนวคิดจาก: src/conversation/handleMessage.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8a: TurnContext + prepare) — consent อ่านแล้วเก็บไว้เฉยๆ (flow รอคำตอบ ไม่บล็อก);
       รูปเก็บ MinIO แล้วแต่ยังไม่ส่ง AI (รอคำตอบ: AI API รับรูปได้ไหม) เก็บแค่ storage key
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    ChatTurn,
    Conversation,
    Counters,
    InboundEvent,
    JsonDict,
    StateRecord,
    User,
)
from app.pipeline.steps.fallback import PURPOSE_AI_DOWN
from app.privacy.redact import redact_text
from app.services.storage import resize_image

log = structlog.get_logger(__name__)

# จำนวนข้อความย้อนหลังที่อ่านมาให้ AI (ตรงกับ call_ai.MAX_HISTORY / AiRequest.recent_messages)
HISTORY_LIMIT = 4
# ข้อความขาออกที่ไม่ใช่บทสนทนา ไม่ส่งเป็นแชทย้อนหลัง: "ระบบขัดข้อง" ใน history ทำให้ AI ตอบเพี้ยนซ้ำ
# (วัดจริง 2026-10-26: มี AI_DOWN เป็น assistant turn -> AI ตอบ "Product link: No product link" ทั้งรายการ)
HISTORY_EXCLUDED_TYPES = (PURPOSE_AI_DOWN,)

# ชนิด consent ที่อ่าน — รอคำตอบ: ชนิด/ข้อความ consent จริงและ flow (ตอนนี้อ่านแล้วบันทึกไว้เฉยๆ ไม่บล็อก)
CONSENT_TYPE = "pdpa"

IMAGE_MESSAGE_TYPE = "image"


class TurnContext(BaseModel):
    """ข้อมูลของหนึ่งรอบ (หนึ่ง event) ที่ prepare เตรียมให้ orchestrator — ข้อความในนี้ redact แล้วทั้งหมด

    - state: None ถ้าไม่มีแถว หรือหมดอายุแล้ว (ห้ามใช้ state ที่หมดอายุ)
    - state_expired: เคยมี state แต่หมดอายุ หรือ conversation เพิ่งเปิดใหม่เพราะเงียบเกิน STATE_TTL
    - counters: ตัวนับสัญญาณหลังเกิดเหตุของแถวที่ยังไม่หมดอายุ (หมดอายุ/ไม่มี -> ค่าเริ่มต้น)
    - history: ข้อความล่าสุด (redact แล้ว) ไม่รวมข้อความของ event นี้และ HISTORY_EXCLUDED_TYPES เรียงเก่าไปใหม่
    - image_keys: storage key ของรูปที่เก็บสำเร็จ (ไม่ใช่ตัวรูป)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: InboundEvent
    user: User
    conversation: Conversation
    reopened: bool
    state_record: StateRecord | None
    state: JsonDict | None
    state_expired: bool
    counters: Counters
    text_redacted: str | None
    history: list[ChatTurn] = Field(default_factory=list)
    ai_run_id: int
    inbound_message_id: int
    has_image: bool = False
    image_keys: list[str] = Field(default_factory=list)
    has_consent: bool = False


def image_storage_key(user_id: int, raw_event_id: int) -> str:
    """key ของรูปใน MinIO — ใช้ id ภายในเท่านั้น (ไม่มีข้อมูลส่วนบุคคล) และคงที่ต่อ event (retry ทับไฟล์เดิม)"""
    return f"img/{user_id}/{raw_event_id}.jpg"


def inbound_message_type(event: InboundEvent) -> str:
    """messages.message_type ของข้อความขาเข้า: ชนิดข้อความ LINE (text/image/sticker/...) หรือชนิด event (postback)"""
    return event.message_type or event.event_type.value


async def _store_image(deps: Deps, event: InboundEvent, user: User, message_id: int) -> str | None:
    """ดึงรูปจาก LINE -> ย่อ (ตัด EXIF) -> เก็บ MinIO -> บันทึก attachments; คืน key หรือ None ถ้าพัง

    พังตรงไหนก็ log แล้วไปต่อ (รูปเสียไม่ควรทำให้ลูกค้าไม่ได้คำตอบ)
    retry event เดิม: put_image ทับไฟล์เดิม แต่ add_attachment ชน UNIQUE storage_key -> log แล้วไปต่อ
    """
    if not event.line_message_id:
        log.warning("prepare.image_without_message_id")
        return None
    key = image_storage_key(user.id, event.raw_event_id)
    try:
        content = await deps.line.get_content(event.line_message_id)
        data, content_type = await resize_image(content.data)
        await deps.storage.put_image(key, data, content_type)
        await deps.messages.add_attachment(
            message_id=message_id,
            storage_key=key,
            content_type=content_type,
            size_bytes=len(data),
        )
    except Exception as e:
        # ไม่ log ข้อความ error ดิบ (อาจมี URL/ข้อมูลของ LINE) เก็บแค่ชนิด
        log.warning("prepare.image_store_failed", error_class=type(e).__name__)
        return None
    return key


async def prepare(deps: Deps, event: InboundEvent) -> TurnContext:
    """ขั้น 3 ตามลำดับ: user -> conversation -> ข้อความขาเข้า (redact) -> รูป -> ai_runs -> state -> แชทย้อนหลัง -> consent

    ทุกขั้น idempotent ต่อ raw_event_id (retry ได้): add_inbound / create_run คืนแถวเดิม
    """
    user = await deps.users.get_or_create(event.line_user_id)
    conv, reopened = await deps.conversations.get_or_open(user.id, deps.settings.state_ttl)

    # redact ก่อนเก็บและก่อนส่ง AI — ข้อความดิบ (event.text) ไม่ลง DB ไม่ลง log
    text_redacted = redact_text(event.text) if event.text is not None else None
    message_id = await deps.messages.add_inbound(
        conversation_id=conv.id,
        raw_event_id=event.raw_event_id,
        message_type=inbound_message_type(event),
        text_redacted=text_redacted,
    )
    # ไม่เลื่อน handoff_started_at (ข้อความระหว่าง human ไม่ต่อ 6 ชม.) — repo รับประกัน
    await deps.conversations.touch_user_message(conv.id)

    # รูป: เก็บเสมอ (รวมระหว่าง human) แต่ยังไม่ส่ง AI — รอคำตอบ: AI API รับรูปได้ไหม
    has_image = event.message_type == IMAGE_MESSAGE_TYPE
    image_keys: list[str] = []
    if has_image:
        key = await _store_image(deps, event, user, message_id)
        if key is not None:
            image_keys.append(key)

    ai_run_id = await deps.ai_runs.create_run(raw_event_id=event.raw_event_id, conversation_id=conv.id)

    record = await deps.state.get(conv.id)
    expired = record is not None and record.is_expired(deps.clock.now())
    live = record if record is not None and not expired else None
    state = dict(live.state) if live is not None and live.state is not None else None
    counters = live.counters if live is not None else Counters()
    # conversation เพิ่งเปิดใหม่เพราะเงียบเกิน STATE_TTL = ความจำเดิมหมดอายุ (state เก่าอยู่กับ conversation เก่า)
    state_expired = expired or reopened

    history = await deps.messages.recent_texts_redacted(
        conv.id,
        limit=HISTORY_LIMIT,
        exclude_raw_event_id=event.raw_event_id,
        exclude_types=HISTORY_EXCLUDED_TYPES,
    )

    # consent: อ่านและบันทึกไว้ใน context เท่านั้น — รอคำตอบ: flow ขอ consent (ไม่บล็อกการตอบ)
    has_consent = await deps.users.has_consent(user.id, CONSENT_TYPE)

    return TurnContext(
        event=event,
        user=user,
        conversation=conv,
        reopened=reopened,
        state_record=record,
        state=state,
        state_expired=state_expired,
        counters=counters,
        text_redacted=text_redacted,
        history=history,
        ai_run_id=ai_run_id,
        inbound_message_id=message_id,
        has_image=has_image,
        image_keys=image_keys,
        has_consent=has_consent,
    )

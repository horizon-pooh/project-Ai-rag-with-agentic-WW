"""app/pipeline/schemas.py

ชนิดข้อมูล (Pydantic v2) ที่ step รับและคืน

รายละเอียด:
 - TurnContext: raw_event_id, ai_run_id, user, conversation, ข้อความขาเข้า, state, flags (ยังไม่มี — งาน 8 orchestrator)
 - HandoffResult: CONTINUE / RESUMED / ALREADY_BOT / ESCALATE / SILENT + timed_out
 - AiRequest: state (None ถ้าหมดอายุ/เพิ่งล้าง), text_redacted, ข้อความล่าสุด 2-4 (redact), รูปที่ย่อแล้ว (ถ้า API รับ), flags (state_expired, just_resumed, handoff_timed_out)
 - AiResult: kind (products/answer/clarify/cannot_answer), intro, products, clarify_question, escalate_hint, state_out, raw (ตัดความยาว), latency_ms
 - Product: sku, name, price, url, image_url (มาจาก API เท่านั้น AI ห้ามแต่งเอง)
 - OutboundMessage: ชนิดข้อความ LINE ที่ผ่าน validate แล้ว
 - ฟิลด์ของ AiRequest/AiResult ขึ้นกับรูปแบบ AI API (รอคำตอบ)
 - ข้อยกเว้นเรื่องชั้น: โมดูลนี้เป็นข้อมูลล้วน import แค่ stdlib + pydantic ชั้นไหนก็ import ได้
   (api, worker, pipeline, services, repositories) รวมทั้ง error AiUnavailable / AiBadResponse / LineApiError
   ที่ services ยกและ pipeline จับ — ห้าม import โมดูลอื่นของ app จากที่นี่

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว (สัญญางาน 2 ใน docs/PLAN.md) — ฟิลด์ AI ยังรอคำตอบ
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonDict = dict[str, Any]

# --- ขีดจำกัด LINE ที่ validate ใช้ (docs/PLAN.md) ---
LINE_MAX_MESSAGES = 5
LINE_MAX_TEXT_CHARS = 5000
MAX_CAROUSEL_BUBBLES = 10  # จำกัดเอง ต่ำกว่าเพดาน LINE 12
LINE_MAX_QUICK_REPLY_ITEMS = 13
LINE_MAX_ALT_TEXT_CHARS = 400

# ความยาว raw ของ AI ที่เก็บไว้ debug
AI_RAW_MAX_CHARS = 4000


class _Frozen(BaseModel):
    """ฐานของ record ภายใน: แก้ค่าไม่ได้ (ใช้ model_copy(update=...)) และห้ามฟิลด์เกิน (กันพิมพ์ชื่อผิด)"""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --- ค่าคงที่ (ตรงกับ CHECK ใน migrations/20261022000000_init.sql) ---


class ConversationStatus(StrEnum):
    BOT = "bot"
    HUMAN = "human"


class ProcessStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class HandoffEndReason(StrEnum):
    USER_RESUMED = "user_resumed"
    TIMEOUT = "timeout"
    STAFF_CLOSED = "staff_closed"


class EventType(StrEnum):
    MESSAGE = "message"
    POSTBACK = "postback"
    FOLLOW = "follow"
    UNFOLLOW = "unfollow"
    OTHER = "other"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class OutboundStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DROPPED = "dropped"
    FAILED = "failed"


class FeedbackKind(StrEnum):
    THUMB_UP = "thumb_up"
    THUMB_DOWN = "thumb_down"
    CARD_CLICK = "card_click"
    REVIEW_LABEL = "review_label"


class PostbackAction(StrEnum):
    """ค่า action ใน postback data (querystring) — ดู docs/PLAN.md ค่าคงที่"""

    TALK_TO_STAFF = "talk_to_staff"
    RESUME_AI = "resume_ai"
    RESTART = "restart"
    DETAIL = "detail"  # + sku=<SKU>
    FEEDBACK = "feedback"  # + v=up|down & run=<ai_run_id>


def postback_data(action: PostbackAction, **params: str | int) -> str:
    """สร้าง postback data เช่น postback_data(PostbackAction.DETAIL, sku="A1") -> "action=detail&sku=A1" """
    return urlencode({"action": action.value, **{k: str(v) for k, v in params.items()}})


def parse_postback_data(data: str | None) -> dict[str, str]:
    """แปลง postback data (querystring) เป็น dict; key ซ้ำ -> ใช้ตัวท้าย"""
    if not data:
        return {}
    return dict(parse_qsl(data, keep_blank_values=True))


def hash_token(token: str) -> str:
    """hash ของ token ลิงก์ /close (sha256 hex) — DB เก็บแค่ค่านี้ ไม่เก็บ token จริง"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- record จาก DB ---


class User(_Frozen):
    id: int
    line_user_id: str
    display_name: str | None = None
    created_at: datetime
    last_seen_at: datetime
    deleted_at: datetime | None = None


class Conversation(_Frozen):
    id: int
    user_id: int
    status: ConversationStatus
    opened_at: datetime
    last_user_message_at: datetime | None = None
    closed_at: datetime | None = None
    handoff_started_at: datetime | None = None
    handoff_reason: str | None = None
    handoff_ended_at: datetime | None = None
    handoff_end_reason: HandoffEndReason | None = None
    outcome: Literal["resolved", "handed_off", "abandoned", "unknown"] = "unknown"
    outcome_source: Literal["feedback", "heuristic", "staff_label", "reviewed"] | None = None
    staff_label: Literal["correct", "over_escalated", "user_requested"] | None = None


class RawEvent(_Frozen):
    """แถวของ line_raw_events (payload = event ของ LINE ที่ตัด replyToken แล้ว)"""

    id: int
    webhook_event_id: str
    line_user_id: str | None
    event_type: str
    payload: JsonDict
    occurred_at: datetime
    received_at: datetime
    process_status: ProcessStatus
    claimed_at: datetime | None = None
    processed_at: datetime | None = None
    attempts: int = 0
    error: str | None = None

    def to_inbound(self) -> InboundEvent:
        return InboundEvent.from_line_event(self.id, self.payload)


class CloseToken(_Frozen):
    """ผลของ consume_close_token: เคสไหน รอบส่งต่อไหน (ส่งต่อให้ end_handoff เป็น expected_started_at)"""

    conversation_id: int
    handoff_started_at: datetime


class ChatTurn(_Frozen):
    """ข้อความหนึ่งข้อความในแชทย้อนหลัง (redact แล้ว) ส่งให้ AI ได้"""

    direction: Direction
    text_redacted: str


class OutboundReservation(_Frozen):
    """ผลของ reserve_outbound: already_sent=True -> แถวเดิมส่งสำเร็จแล้ว ห้ามส่งซ้ำ"""

    message_id: int
    already_sent: bool


class AttachmentRef(_Frozen):
    """แถว attachments ที่ต้องลบไฟล์ใน Storage (retention รูป) — storage_key ไม่มีข้อมูลส่วนบุคคล"""

    id: int
    storage_key: str


class MediaContent(_Frozen):
    """เนื้อหารูป/ไฟล์ที่ดึงจาก LINE"""

    data: bytes
    content_type: str


class AiStepRecord(_Frozen):
    """หนึ่งแถวของ ai_steps (orchestrator สร้าง step ไม่สร้างเอง); input/output redact แล้ว รูปเก็บแค่ id"""

    ai_run_id: int
    raw_event_id: int | None
    seq: int
    step: str
    input: JsonDict | None = None
    output: JsonDict | None = None
    state_before: JsonDict | None = None
    state_after: JsonDict | None = None
    latency_ms: int | None = None
    attempts: int = 1
    error_class: str | None = None
    cost: Decimal | None = None


# --- เหตุการณ์ขาเข้า ---


class InboundEvent(_Frozen):
    """event ของ LINE ที่แปลงแล้ว (จาก line_raw_events.payload) — ไม่มี replyToken (อยู่ใน ReplyTokens)"""

    raw_event_id: int
    webhook_event_id: str
    line_user_id: str
    event_type: EventType
    message_type: str | None = None  # text / image / sticker / ... (เฉพาะ event_type=message)
    text: str | None = None  # ข้อความดิบ (ยังไม่ redact) — ห้ามส่ง AI / ห้ามลง log
    postback_data: str | None = None
    postback_params: dict[str, str] = Field(default_factory=dict)  # postback_data ที่แปลงจาก querystring
    line_message_id: str | None = None
    occurred_at: datetime

    @property
    def postback_action(self) -> str | None:
        return self.postback_params.get("action")

    @classmethod
    def from_line_event(cls, raw_event_id: int, event: Mapping[str, Any]) -> InboundEvent:
        """แปลง event JSON ของ LINE webhook

        ยก ValueError ถ้าไม่มี source.userId (เช่น event จากกลุ่ม) หรือไม่มี webhookEventId / timestamp
        — worker ตั้ง process_status = skipped
        """
        source = event.get("source") or {}
        line_user_id = source.get("userId")
        if not line_user_id:
            raise ValueError("event ไม่มี source.userId")
        webhook_event_id = event.get("webhookEventId")
        if not webhook_event_id:
            raise ValueError("event ไม่มี webhookEventId")
        ts = event.get("timestamp")
        if not isinstance(ts, int | float):
            raise ValueError("event ไม่มี timestamp")
        raw_type = event.get("type")
        try:
            event_type = EventType(raw_type)
        except ValueError:
            event_type = EventType.OTHER

        message_type = text = line_message_id = postback = None
        if event_type is EventType.MESSAGE:
            message = event.get("message") or {}
            message_type = message.get("type")
            line_message_id = message.get("id")
            if message_type == "text":
                text = message.get("text")
        elif event_type is EventType.POSTBACK:
            postback = (event.get("postback") or {}).get("data")

        return cls(
            raw_event_id=raw_event_id,
            webhook_event_id=webhook_event_id,
            line_user_id=line_user_id,
            event_type=event_type,
            message_type=message_type,
            text=text,
            postback_data=postback,
            postback_params=parse_postback_data(postback),
            line_message_id=line_message_id,
            occurred_at=datetime.fromtimestamp(ts / 1000, tz=UTC),
        )


# --- ความจำ ---


class Product(BaseModel):
    """สินค้าจาก AI API (ค้นด้วย hybrid search ฝั่ง API) — การ์ดสร้างจากข้อมูลนี้เท่านั้น"""

    # ฟิลด์เกินจาก API ไม่ทำให้พัง (ignore)
    model_config = ConfigDict(frozen=True, extra="ignore")

    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: Decimal | None = None
    url: str
    image_url: str | None = None


class Counters(_Frozen):
    """ตัวนับสัญญาณหลังเกิดเหตุ (conversation_state.counters) — ไม่ส่ง AI"""

    clarify_streak: int = Field(default=0, ge=0)
    cannot_answer_streak: int = Field(default=0, ge=0)
    validate_fail_streak: int = Field(default=0, ge=0)
    last_products: list[Product] = Field(default_factory=list)  # ใช้ตอบปุ่ม "ดูรายละเอียด" (action=detail)


class StateRecord(_Frozen):
    """แถวของ conversation_state ตามที่อยู่ใน DB (อาจหมดอายุแล้ว — prepare เป็นคนตัดสิน)"""

    conversation_id: int
    state: JsonDict | None = None  # ส่ง AI
    counters: Counters = Field(default_factory=Counters)  # ไม่ส่ง AI
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now


# --- AI ---


class AiKind(StrEnum):
    PRODUCTS = "products"
    ANSWER = "answer"
    CLARIFY = "clarify"
    CANNOT_ANSWER = "cannot_answer"


class AiFlags(_Frozen):
    state_expired: bool = False
    just_resumed: bool = False
    handoff_timed_out: bool = False


class AiRequest(_Frozen):
    """สิ่งที่ส่งให้ AI API (สัญญาเสนอ — รอคำตอบ: รูปแบบจริงของ API) ทุกข้อความต้อง redact แล้ว"""

    state: JsonDict | None = None  # None ถ้าหมดอายุ/เพิ่งล้าง
    text_redacted: str
    recent_messages: list[ChatTurn] = Field(default_factory=list, max_length=4)  # ข้อความล่าสุด 2-4 เก่าไปใหม่
    # id ของรูป (attachments.storage_key) ที่ย่อแล้ว — รอคำตอบ: AI API รับรูปได้ไหม (ตอนนี้ยังไม่ส่ง)
    images: list[str] = Field(default_factory=list)
    flags: AiFlags = Field(default_factory=AiFlags)


class AiResult(BaseModel):
    """ผลจาก AI API ที่แปลงแล้ว (ai_parsers) — โค้ดตัดสินเส้นทางเอง"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: AiKind
    intro: str | None = None
    products: list[Product] = Field(default_factory=list)
    clarify_question: str | None = None
    escalate_hint: bool | None = None  # None = API ไม่ได้บอก — รอคำตอบ
    state_out: JsonDict | None = None  # None = API ไม่คืน state — รอคำตอบ
    raw: str = ""  # คำตอบดิบ ตัดเหลือ AI_RAW_MAX_CHARS
    latency_ms: int = Field(default=0, ge=0)
    attempts: int = Field(default=1, ge=1)

    @field_validator("raw", mode="before")
    @classmethod
    def _truncate_raw(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) > AI_RAW_MAX_CHARS:
            return v[:AI_RAW_MAX_CHARS] + "…[truncated]"
        return v


class AiError(Exception):
    """ฐานของ error จาก AI client; error_class/attempts/latency_ms ลง ai_steps"""

    def __init__(
        self, message: str, *, error_class: str, attempts: int = 1, latency_ms: int | None = None
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.attempts = attempts
        self.latency_ms = latency_ms


class AiUnavailable(AiError):
    """timeout / ต่อไม่ได้ / 429 / 5xx ที่ลองซ้ำแล้ว (หรือเวลาไม่พอลองซ้ำ) -> fallback"""


class AiBadResponse(AiError):
    """4xx อื่น หรืออ่านผลไม่ออก (ไม่ลองซ้ำ) -> fallback"""


class LineApiError(Exception):
    """error จาก LINE Messaging API (line_client ยก send จับ)

    retryable: True สำหรับ timeout / ต่อไม่ได้ / 5xx / 429; reply ที่ replyToken ใช้ไม่ได้ -> status_code=400, retryable=False
    """

    def __init__(self, message: str, *, status_code: int | None, retryable: bool) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


# --- ผลของแต่ละ step ---


class HandoffOutcome(StrEnum):
    RESUMED = "resumed"
    ALREADY_BOT = "already_bot"
    ESCALATE = "escalate"
    SILENT = "silent"
    CONTINUE = "continue"


class HandoffResult(_Frozen):
    outcome: HandoffOutcome
    timed_out: bool = False  # เพิ่งหมดเวลา 6 ชม. ในรอบนี้ (ข้อ 1 ของ handoff_check)


class RuleOutcome(StrEnum):
    ESCALATE = "escalate"  # ร้องเรียน/เคลม/ขอคน -> ส่งต่อทันที ไม่เรียก AI
    REPLY = "reply"  # FAQ/ทักทาย -> ตอบ reply_text
    IGNORE = "ignore"  # keyword ของ Vendor 1 -> ไม่ตอบ
    RATE_LIMITED = "rate_limited"  # ถี่ผิดปกติ
    EMPTY = "empty"  # ข้อความว่าง / ไม่ใช่ข้อความที่ตอบได้
    PRODUCT_DETAIL = "product_detail"  # postback action=detail -> sku
    RESTART = "restart"  # postback action=restart
    FEEDBACK = "feedback"  # postback action=feedback
    CONTINUE = "continue"  # ไป call_ai


class RuleResult(_Frozen):
    outcome: RuleOutcome
    reason: str | None = None  # เช่น รายการใน escalate.txt ที่ตรง (ลง ai_runs.reason)
    reply_text: str | None = None  # สำหรับ REPLY
    sku: str | None = None  # สำหรับ PRODUCT_DETAIL


class OutboundMessage(_Frozen):
    """ข้อความขาออกหนึ่งชุด (หนึ่งครั้ง reply/push) — messages = LINE message objects"""

    messages: list[JsonDict] = Field(min_length=1, max_length=LINE_MAX_MESSAGES)
    purpose: str = Field(min_length=1)  # เช่น ai_answer, resumed, handoff — ลง messages.message_type


class SendStatus(StrEnum):
    SENT = "sent"
    DROPPED_HUMAN = "dropped_human"
    DROPPED_KILL_SWITCH = "dropped_kill_switch"
    DUPLICATE = "duplicate"  # dedupe_key นี้ส่งไปแล้ว
    FAILED = "failed"


class SendResult(_Frozen):
    status: SendStatus
    via: Literal["reply", "push"] | None = None
    message_id: int | None = None


class ValidationResult(_Frozen):
    ok: bool
    outbound: OutboundMessage | None = None
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ValidationResult:
        if self.ok and self.outbound is None:
            raise ValueError("ok=True ต้องมี outbound")
        if not self.ok and not self.errors:
            raise ValueError("ok=False ต้องมี errors อย่างน้อยหนึ่งข้อ")
        return self

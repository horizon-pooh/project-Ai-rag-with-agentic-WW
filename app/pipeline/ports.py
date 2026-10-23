"""app/pipeline/ports.py

พอร์ต (typing.Protocol) ที่ pipeline รู้จัก — repositories และ services implement ตามนี้

รายละเอียด:
 - pipeline ไม่เขียน SQL และไม่เรียก LINE เอง: รับ Deps (รวมทุกพอร์ต + Settings) จากข้างนอก
 - repositories/* implement พอร์ต *Repo (SQL จริงอยู่ที่นั่นที่เดียว); services/* implement ที่เหลือ
 - เทสต์ใช้ fake ใน tests/fakes.py ที่ทำตามสัญญา (docstring) ของแต่ละเมธอดเหมือนกัน
 - ชื่อเมธอดและ signature เป็นสัญญาของงาน 2 (docs/PLAN.md) ถ้าต้องเปลี่ยนให้รายงาน orchestrator
 - เวลา: repositories ประทับเวลาด้วย now() ของ DB; fake ใช้ Clock ตัวเดียวกับ Deps.clock
 - "UPDATE แบบมีเงื่อนไข" = ใครมาก่อนชนะ คนที่ได้ 0 แถวได้ None และต้องไม่ทำอะไรต่อ (ไม่ส่งข้อความ/ไม่แจ้งซ้ำ)
 - ชนิดข้อมูลทั้งหมดมาจาก app/pipeline/schemas.py

แนวคิดจาก: ใหม่ (docs/PLAN.md หัวข้อ พอร์ต)
สถานะ: มีโค้ดแล้ว (สัญญางาน 2)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.config import Settings
from app.pipeline.schemas import (
    AiRequest,
    AiResult,
    AiStepRecord,
    AttachmentRef,
    ChatTurn,
    CloseToken,
    Conversation,
    Counters,
    FeedbackKind,
    HandoffEndReason,
    JsonDict,
    MediaContent,
    OutboundMessage,
    OutboundReservation,
    OutboundStatus,
    ProcessStatus,
    RawEvent,
    StateRecord,
    User,
)

# --- repositories ---


@runtime_checkable
class RawEventsRepo(Protocol):
    """ตาราง line_raw_events — ความจริงของงานในคิว (Redis เก็บแค่ id)"""

    async def insert(
        self,
        *,
        webhook_event_id: str,
        line_user_id: str | None,
        event_type: str,
        payload: JsonDict,
        occurred_at: datetime,
    ) -> int | None:
        """INSERT ... ON CONFLICT (webhook_event_id) DO NOTHING RETURNING id

        คืน id ใหม่; คืน None ถ้า webhook_event_id ซ้ำ (LINE ส่งซ้ำ) — ผู้เรียกไม่ต้องใส่คิวซ้ำ
        payload ต้องตัด replyToken ออกแล้ว (มีอยู่ -> ValueError / DB CHECK ปฏิเสธ)
        """
        ...

    async def get(self, raw_event_id: int) -> RawEvent | None:
        """อ่านหนึ่งแถว ไม่มี -> None"""
        ...

    async def claim(self, raw_event_id: int) -> RawEvent | None:
        """UPDATE line_raw_events SET process_status='processing', claimed_at=now(), attempts=attempts+1
        WHERE id=$1 AND process_status='pending' RETURNING *

        ได้แถว -> ผู้เรียกเป็นคนทำ event นี้; None -> มีคนทำแล้ว/ทำเสร็จแล้ว ห้ามทำต่อ
        worker และ fast path ใช้ทางเดียวกันนี้
        """
        ...

    async def mark_done(self, raw_event_id: int, status: ProcessStatus = ProcessStatus.DONE) -> None:
        """ตั้งผลจบ: status ต้องเป็น DONE หรือ SKIPPED (อื่น -> ValueError); processed_at=now(), error=NULL"""
        ...

    async def mark_failed(self, raw_event_id: int, error: str) -> None:
        """ตั้ง process_status='failed', processed_at=now(), error (redact แล้ว ตัดความยาวแล้ว) — failed เป็นผลจบ"""
        ...

    async def release(self, raw_event_id: int, error: str) -> None:
        """processing -> pending (WHERE process_status='processing') เพื่อให้ลองใหม่ เก็บ error ล่าสุดไว้

        ใช้เมื่อพังแบบชั่วคราว (เช่น DB/LINE ล่มกลางทาง) แล้วอยากให้คิว retry; attempts ไม่ลด
        """
        ...

    async def list_pending_ids_for_user(self, line_user_id: str) -> list[int]:
        """id ของ event ที่ยัง pending ของ user นี้ เรียง occurred_at, id (เก่าสุดก่อน)"""
        ...

    async def list_stale(self, received_before: datetime, limit: int = 100) -> list[int]:
        """id ที่ยัง pending และ received_at < received_before เรียงเก่าสุดก่อน (cron ใส่คิวใหม่ด้วย job id เดิม)"""
        ...

    async def release_stuck(self, claimed_before: datetime, limit: int = 100) -> list[int]:
        """UPDATE ... SET process_status='pending' WHERE process_status='processing' AND claimed_at < $1 RETURNING id

        คืน id ที่ปล่อยแล้ว (Worker ตายกลางทาง) ให้ cron ใส่คิวใหม่; claimed_before ต้องเก่ากว่า
        TURN_DEADLINE_S + เวลาล็อกพอสมควร ไม่งั้นงานที่ยังรันอยู่จะถูกปล่อย
        """
        ...


@runtime_checkable
class UsersRepo(Protocol):
    """ตาราง line_users / line_consents"""

    async def get_or_create(self, line_user_id: str, display_name: str | None = None) -> User:
        """หา user จาก line_user_id ไม่มีก็สร้าง; ทุกครั้งตั้ง last_seen_at=now() (display_name ไม่ None -> อัปเดต)"""
        ...

    async def has_consent(self, user_id: int, consent_type: str) -> bool:
        """แถวล่าสุด (created_at) ของ consent_type นี้ granted=True หรือไม่; ไม่มีแถว -> False"""
        ...

    async def get_line_user_id(self, user_id: int) -> str | None:
        """line_user_id ของ user (Conversation มีแค่ user_id — cron handoff_timeout / /close ใช้ push หาลูกค้า)

        ไม่มีแถว หรือ deleted_at ไม่เป็น NULL -> None (ผู้เรียกไม่ส่งข้อความ)
        """
        ...

    async def delete_user(self, line_user_id: str) -> list[str] | None:
        """ลบตาม PDPA ที่จุดเดียว — transaction เดียว:
        1) เก็บ storage_key ของ attachments ของ user นี้ที่ยังไม่ลบ (deleted_at IS NULL)
        2) DELETE FROM line_raw_events WHERE line_user_id=$1 (ไม่มี FK ไป line_users ต้องลบแยก — ทำเสมอแม้ไม่มี user)
        3) DELETE FROM line_users WHERE line_user_id=$1 (ON DELETE CASCADE ลบที่เหลือทั้งหมด)

        คืน storage_key ให้ผู้เรียกลบไฟล์ใน Storage ต่อ (DB เป็นความจริง ลบไฟล์ไม่ได้ไม่ย้อน DB)
        None = ไม่มี user นี้ใน line_users
        """
        ...


@runtime_checkable
class ConversationsRepo(Protocol):
    """ตาราง conversations / close_tokens — จุดเดียวที่เปลี่ยน status bot <-> human"""

    async def get(self, conversation_id: int) -> Conversation | None:
        """อ่านสถานะล่าสุด (sendGuard เรียกก่อนส่งทุกครั้ง)"""
        ...

    async def get_or_open(self, user_id: int, idle_ttl: timedelta) -> tuple[Conversation, bool]:
        """คืน (conversation ที่เปิดอยู่, reopened)

        - ไม่มีอันที่เปิด -> สร้างใหม่ (status='bot') reopened=False
        - เปิดอยู่และ status='human' -> คืนอันเดิมเสมอ (ห้ามเปิดใหม่ทับเคสที่พนักงานดูแล)
        - เปิดอยู่และ status='bot' และ greatest(opened_at, last_user_message_at, handoff_ended_at) <= now() - idle_ttl
          -> ปิดอันเดิม (closed_at=now()) แล้วสร้างใหม่ reopened=True (prepare ใช้ตั้ง state_expired)
        - ไม่งั้นคืนอันเดิม reopened=False
        ทำใน transaction เดียว; unique index (user_id) WHERE closed_at IS NULL กันเปิดซ้อน
        """
        ...

    async def touch_user_message(self, conversation_id: int) -> None:
        """ตั้ง last_user_message_at=now() (ไม่แตะ handoff_started_at — ข้อความระหว่าง human ไม่เลื่อน 6 ชม.)"""
        ...

    async def start_handoff(self, conversation_id: int, reason: str) -> Conversation | None:
        """UPDATE conversations SET status='human', handoff_started_at=now(), handoff_reason=$2,
        handoff_ended_at=NULL, handoff_end_reason=NULL
        WHERE id=$1 AND status='bot' AND closed_at IS NULL RETURNING *

        None = 0 แถว (เป็น human อยู่แล้ว เช่น fast path ทำไปก่อน) -> ไม่แจ้ง call center ซ้ำ
        """
        ...

    async def end_handoff(
        self, conversation_id: int, reason: HandoffEndReason, expected_started_at: datetime
    ) -> Conversation | None:
        """UPDATE conversations SET status='bot', handoff_ended_at=now(), handoff_end_reason=$2
        WHERE id=$1 AND status='human' AND handoff_started_at=$3 RETURNING *

        expected_started_at = handoff_started_at ที่ผู้เรียกเห็น (กันปิดรอบใหม่ด้วยคำสั่งของรอบเก่า เช่นลิงก์ /close เก่า)
        สำเร็จ (1 แถว) -> ลบ conversation_state ของ conversation นี้ใน transaction เดียวกัน แล้วคืนแถวใหม่
        None = 0 แถว (มีทางอื่นปิดไปก่อน / รอบไม่ตรง) -> ผู้เรียกไม่ส่งข้อความ ไม่แจ้งพนักงาน
        """
        ...

    async def list_handoff_timed_out(self, ttl: timedelta, limit: int = 100) -> list[Conversation]:
        """status='human' AND handoff_started_at <= now() - ttl เรียงเก่าสุดก่อน (ให้ cron handoff_timeout)"""
        ...

    async def list_open_human(self, limit: int = 50) -> list[Conversation]:
        """status='human' ทั้งหมด (ไม่สนอายุ) เรียงส่งต่อล่าสุดก่อน — ให้คำสั่งผู้ดูแล (admin conversations)"""
        ...

    async def create_close_token(
        self, conversation_id: int, handoff_started_at: datetime, ttl: timedelta
    ) -> str:
        """สร้าง token สุ่ม (secrets.token_urlsafe) เก็บแค่ hash_token(token) + expires_at=now()+ttl คืน token จริง
        (ใช้ต่อเป็นลิงก์ PUBLIC_BASE_URL/close?token=...) — token จริงห้ามลง log/DB"""
        ...

    async def consume_close_token(self, token: str) -> CloseToken | None:
        """UPDATE close_tokens SET used_at=now()
        WHERE token_hash=hash_token($1) AND used_at IS NULL AND expires_at > now()
        RETURNING conversation_id, handoff_started_at

        ใช้ได้ครั้งเดียว; None = ไม่มี / ใช้แล้ว / หมดอายุ. สำเร็จแล้วผู้เรียกต้อง end_handoff(STAFF_CLOSED,
        expected_started_at=ผลนี้.handoff_started_at) ซึ่งอาจได้ None (เคสปิดไปแล้วทางอื่น)
        """
        ...


@runtime_checkable
class StateRepo(Protocol):
    """ตาราง conversation_state"""

    async def get(self, conversation_id: int) -> StateRecord | None:
        """คืนแถวแม้หมดอายุแล้ว (prepare ใช้ StateRecord.is_expired ตัดสินและบอกลูกค้า); ไม่มีแถว -> None"""
        ...

    async def save(
        self,
        conversation_id: int,
        *,
        state: JsonDict | None,
        counters: Counters,
        expires_at: datetime,
    ) -> None:
        """UPSERT ทั้งแถว (state, counters, expires_at, updated_at=now()) — ไม่เรียกระหว่าง human"""
        ...

    async def clear(self, conversation_id: int) -> None:
        """ลบแถว (ไม่มีก็ไม่เป็นไร) เช่น action=restart"""
        ...


@runtime_checkable
class MessagesRepo(Protocol):
    """ตาราง messages / attachments"""

    async def add_inbound(
        self,
        *,
        conversation_id: int,
        raw_event_id: int,
        message_type: str,
        text_redacted: str | None,
    ) -> int:
        """บันทึกข้อความขาเข้า (direction='in', dedupe_key='in:<raw_event_id>') คืน message id

        idempotent: retry event เดิม -> คืน id แถวเดิม ไม่สร้างซ้ำ
        """
        ...

    async def recent_texts_redacted(
        self, conversation_id: int, limit: int = 4, *, exclude_raw_event_id: int | None = None
    ) -> list[ChatTurn]:
        """ข้อความล่าสุดไม่เกิน limit ที่มี text_redacted (ขาเข้า + ขาออกที่ status='sent') เรียงเก่าไปใหม่

        exclude_raw_event_id: ไม่เอาข้อความขาเข้าของ event นี้ (ข้อความปัจจุบันส่งแยกใน AiRequest.text_redacted)
        """
        ...

    async def count_inbound_since(self, conversation_id: int, since: datetime) -> int:
        """จำนวนข้อความขาเข้าที่ created_at >= since (ใช้จำกัดอัตรา RATE_LIMIT_PER_MIN)"""
        ...

    async def reserve_outbound(
        self,
        *,
        conversation_id: int,
        raw_event_id: int | None,
        dedupe_key: str,
        outbound: OutboundMessage,
        text_redacted: str | None = None,
    ) -> OutboundReservation:
        """บันทึกข้อความขาออกก่อนส่ง (status='pending', message_type=outbound.purpose, payload=outbound.messages)

        INSERT ... ON CONFLICT (dedupe_key) DO NOTHING; ชนกัน -> คืนแถวเดิม
        already_sent=True เฉพาะเมื่อแถวเดิม status='sent' -> ผู้เรียกห้ามส่งซ้ำ (SendStatus.DUPLICATE)
        แถวเดิม pending/failed/dropped -> already_sent=False (ผู้เรียกตรวจ guard แล้วส่งใหม่ได้)
        dedupe_key ต้องคงที่ต่อ (event, ข้อความชุดนั้น) เช่น 'out:<raw_event_id>:<purpose>'
        """
        ...

    async def mark_outbound(
        self, message_id: int, status: OutboundStatus, *, reason: str | None = None
    ) -> None:
        """ตั้งผลของข้อความขาออก: SENT -> sent_at=now(); DROPPED/FAILED -> drop_reason=reason"""
        ...

    async def add_attachment(
        self, *, message_id: int, storage_key: str, content_type: str, size_bytes: int
    ) -> int:
        """บันทึกไฟล์ที่เก็บใน Storage แล้ว คืน attachment id; storage_key ซ้ำ (retry) -> คืน id แถวเดิม"""
        ...

    async def list_expired_attachments(self, before: datetime, limit: int = 500) -> list[AttachmentRef]:
        """attachments ที่ created_at < before และ deleted_at IS NULL เรียงเก่าสุดก่อน ไม่เกิน limit (retention รูป)"""
        ...

    async def mark_attachment_deleted(self, attachment_id: int) -> None:
        """ตั้ง deleted_at=now() (WHERE deleted_at IS NULL) — เรียกหลังลบไฟล์ใน Storage สำเร็จเท่านั้น"""
        ...

    async def anonymize_older_than(self, before: datetime, limit: int = 5000) -> int:
        """SET text_redacted=NULL, payload=NULL ของ messages ที่ created_at < before ไม่เกิน limit แถว คืนจำนวนแถว

        idempotent: แตะเฉพาะแถวที่ยังมีค่าอย่างน้อยหนึ่งช่อง (ผู้เรียกวนจนได้ < limit)
        """
        ...


@runtime_checkable
class AiRunsRepo(Protocol):
    """ตาราง ai_runs / ai_steps"""

    async def create_run(self, *, raw_event_id: int, conversation_id: int) -> int:
        """สร้าง ai_runs หนึ่งแถวต่อ event คืน id; idempotent ด้วย UNIQUE raw_event_id (retry -> id เดิม)"""
        ...

    async def add_step(self, step: AiStepRecord) -> None:
        """เพิ่มหนึ่งแถว ai_steps (orchestrator เรียกเท่านั้น) — retry ซ้ำ seq เดิมได้ ไม่มี UNIQUE"""
        ...

    async def finish_run(
        self,
        ai_run_id: int,
        *,
        route: str,
        reason: str | None = None,
        confidence: Decimal | None = None,
    ) -> None:
        """ตั้ง finished_at=now(), route (เช่น ai_answer / rule_escalate / silent), reason, confidence"""
        ...

    async def anonymize_steps_older_than(self, before: datetime, limit: int = 5000) -> int:
        """SET input/output/state_before/state_after=NULL ของ ai_steps ที่ created_at < before ไม่เกิน limit แถว
        คืนจำนวนแถว — idempotent: แตะเฉพาะแถวที่ยังมีค่าอย่างน้อยหนึ่งช่อง (ผู้เรียกวนจนได้ < limit)"""
        ...


@runtime_checkable
class FeedbackRepo(Protocol):
    """ตาราง feedback (สัญญาณแทนความสำเร็จ)"""

    async def add(
        self,
        *,
        conversation_id: int,
        kind: FeedbackKind,
        value: str | None = None,
        ai_run_id: int | None = None,
    ) -> int:
        """บันทึกหนึ่งแถว คืน id; ai_run_id มาจาก postback (ลูกค้าแก้ได้) — ไม่มีอยู่/ไม่ใช่ของ conversation นี้
        -> เก็บเป็น NULL ไม่ยก error"""
        ...


@runtime_checkable
class FlagsRepo(Protocol):
    """ตาราง ops_flags (เช่น kill_switch) — เปลี่ยนตอนรันได้ไม่ต้อง deploy"""

    async def get_flag(self, key: str) -> str | None:
        """ค่าของ key; ไม่มี -> None"""
        ...

    async def set_flag(self, key: str, value: str) -> None:
        """UPSERT ค่า + updated_at=now()"""
        ...


# --- services ---


@runtime_checkable
class AiClient(Protocol):
    """AI API ภายนอก (services/ai_client.py) — รอคำตอบ: รูปแบบ API จริง"""

    async def call(self, req: AiRequest, deadline_s: float) -> AiResult:
        """เรียก API ครั้งเดียว (ลองซ้ำได้ 1 ครั้งสำหรับ timeout/ต่อไม่ได้/429/5xx ถ้าเวลาพอ) รวมไม่เกิน deadline_s วินาที

        ยก AiUnavailable (ลองแล้วยังพัง/หมดเวลา) หรือ AiBadResponse (4xx อื่น/อ่านไม่ออก)
        AiResult.latency_ms / attempts ตั้งโดย client
        """
        ...


@runtime_checkable
class LineClient(Protocol):
    """LINE Messaging API (services/line_client.py) — pipeline เรียกผ่าน steps/send.py เท่านั้น
    (ยกเว้น start_loading / get_content ที่ไม่ใช่ข้อความขาออก)"""

    async def reply(self, reply_token: str, messages: list[JsonDict]) -> None:
        """POST /v2/bot/message/reply; replyToken ใช้ไม่ได้/หมดอายุ -> LineApiError(status_code=400, retryable=False)"""
        ...

    async def push(
        self, line_user_id: str, messages: list[JsonDict], *, retry_key: str | None = None
    ) -> None:
        """POST /v2/bot/message/push; retry_key (UUID) ส่งเป็น X-Line-Retry-Key กันส่งซ้ำตอน retry
        (ผู้เรียกทำ uuid5 จาก dedupe_key) — error -> LineApiError"""
        ...

    async def start_loading(self, line_user_id: str, seconds: int = 20) -> None:
        """loading animation ระหว่างรอ AI (พังไม่เป็นไร: implementation log แล้วกลืน error)"""
        ...

    async def get_content(self, line_message_id: str) -> MediaContent:
        """ดึงเนื้อหารูป/ไฟล์ของข้อความ (มี timeout) — error -> LineApiError"""
        ...


@runtime_checkable
class ReplyTokens(Protocol):
    """Redis key replytoken:{raw_event_id} TTL = REPLY_TOKEN_TTL_S"""

    async def put(self, raw_event_id: int, reply_token: str) -> None:
        """SET replytoken:{raw_event_id} <token> EX REPLY_TOKEN_TTL_S (webhook เรียก)"""
        ...

    async def take(self, raw_event_id: int) -> str | None:
        """GETDEL — ใช้ได้ครั้งเดียว; None = ไม่มี/หมดอายุ/ใช้ไปแล้ว -> ผู้เรียกใช้ push"""
        ...


@runtime_checkable
class Notifier(Protocol):
    """แจ้ง call center (services/notify.py) — ช่องทางรอคำตอบ ตอนนี้ LogNotifier"""

    async def handoff_started(
        self, conv: Conversation, summary: str, close_url: str | None
    ) -> None:
        """แจ้งว่ามีเคสส่งต่อ: summary redact แล้ว, close_url = ลิงก์ /close ใช้ครั้งเดียว (None ถ้าไม่มี PUBLIC_BASE_URL)
        แจ้งไม่สำเร็จ -> log + Sentry ไม่ยก error กลับ"""
        ...

    async def handoff_ended(self, conv: Conversation, reason: HandoffEndReason) -> None:
        """แจ้งว่าลูกค้ากลับไปคุยกับ AI แล้ว (หรือหมดเวลา/พนักงานปิด) ไม่ยก error กลับ"""
        ...


@runtime_checkable
class Storage(Protocol):
    """MinIO (services/storage.py)"""

    async def put_image(self, key: str, data: bytes, content_type: str) -> None:
        """เก็บไฟล์ที่ key (ทับได้ = idempotent) — key ห้ามมีข้อมูลส่วนบุคคล เช่น images/<conversation_id>/<line_message_id>"""
        ...

    async def delete(self, key: str) -> None:
        """ลบไฟล์ (ไม่มีก็ไม่เป็นไร)"""
        ...


@runtime_checkable
class KillSwitch(Protocol):
    """services/kill_switch.py — ops_flags 'kill_switch' OR env KILL_SWITCH; อ่านตอนรันทุกครั้ง"""

    async def is_on(self) -> bool:
        """True -> sendGuard ไม่ส่งข้อความบอท (event ยังบันทึกตามปกติ); อ่านค่าไม่ได้ -> ถือว่า True (ปลอดภัยไว้ก่อน: call center รับแทน) และ log"""
        ...


@runtime_checkable
class Clock(Protocol):
    """เวลาปัจจุบัน (timezone-aware UTC) — เทสต์ใช้ FakeClock ควบคุมเวลา"""

    def now(self) -> datetime: ...


class SystemClock:
    """Clock จริง"""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, kw_only=True)
class Deps:
    """ทุกพอร์ต + Settings ที่ส่งเข้า orchestrator / step (สร้างที่ worker/api; เทสต์ใช้ tests.fakes.make_deps)"""

    settings: Settings
    clock: Clock
    raw_events: RawEventsRepo
    users: UsersRepo
    conversations: ConversationsRepo
    state: StateRepo
    messages: MessagesRepo
    ai_runs: AiRunsRepo
    feedback: FeedbackRepo
    flags: FlagsRepo
    ai: AiClient
    line: LineClient
    reply_tokens: ReplyTokens
    notifier: Notifier
    storage: Storage
    kill_switch: KillSwitch


__all__ = [
    "AiClient",
    "AiRunsRepo",
    "Clock",
    "ConversationsRepo",
    "Deps",
    "FeedbackRepo",
    "FlagsRepo",
    "KillSwitch",
    "LineClient",
    "MessagesRepo",
    "Notifier",
    "RawEventsRepo",
    "ReplyTokens",
    "StateRepo",
    "Storage",
    "SystemClock",
    "UsersRepo",
]

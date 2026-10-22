"""tests/fakes.py

fake ในหน่วยความจำของทุกพอร์ตใน app/pipeline/ports.py ให้เทสต์รันได้โดยไม่ต้องมี Postgres/Redis/LINE/AI

รายละเอียด:
 - ทำตามสัญญา (docstring) ของพอร์ตทุกข้อ รวมเงื่อนไขของ claim / start_handoff / end_handoff /
   reserve_outbound / consume_close_token — ถ้า fake กับ repository จริงทำต่างกัน ถือว่า fake ผิด
 - repository fake ทุกตัวใช้ FakeDb ก้อนเดียวกัน (end_handoff ลบ state ได้เหมือน transaction เดียว)
 - เวลาใช้ FakeClock ตัวเดียวกับ Deps.clock (แทน now() ของ DB)
 - make_deps(...) สร้าง Deps ครบทุกพอร์ต; ส่ง keyword เพื่อแทนตัวใดตัวหนึ่ง
 - make_settings(...) สร้าง Settings ที่มีค่าบังคับครบ ไม่อ่าน .env
 - service fake บันทึกการเรียกไว้ให้เทสต์ตรวจ (FakeLineClient.replies / pushes, FakeNotifier.started ฯลฯ)

แนวคิดจาก: ใหม่ (docs/PLAN.md งาน 2)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    AiKind,
    AiRequest,
    AiResult,
    AiStepRecord,
    ChatTurn,
    CloseToken,
    Conversation,
    ConversationStatus,
    Counters,
    Direction,
    EventType,
    FeedbackKind,
    HandoffEndReason,
    InboundEvent,
    JsonDict,
    LineApiError,
    MediaContent,
    OutboundMessage,
    OutboundReservation,
    OutboundStatus,
    ProcessStatus,
    RawEvent,
    StateRecord,
    User,
    hash_token,
    parse_postback_data,
)

DEFAULT_NOW = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)  # 10:00 เวลาไทย


# --- เวลา ---


class FakeClock:
    """Clock ที่เทสต์ควบคุมได้ (เริ่มที่ DEFAULT_NOW)"""

    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        if now.tzinfo is None:
            raise ValueError("FakeClock ต้องเป็นเวลาที่มี timezone")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> datetime:
        """เลื่อนเวลา เช่น clock.advance(hours=6) หรือ clock.advance(timedelta(minutes=1))"""
        self._now += delta if delta is not None else timedelta(**kwargs)
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


# --- ที่เก็บข้อมูลรวม ---


@dataclass
class MessageRow:
    """แถวของ messages ใน FakeDb (แก้ค่าได้ เหมือนแถวใน DB)"""

    id: int
    conversation_id: int
    raw_event_id: int | None
    direction: Direction
    message_type: str
    text_redacted: str | None
    payload: list[JsonDict] | None
    status: OutboundStatus | None
    drop_reason: str | None
    dedupe_key: str | None
    created_at: datetime
    sent_at: datetime | None = None


@dataclass
class AiRunRow:
    id: int
    raw_event_id: int
    conversation_id: int
    started_at: datetime
    finished_at: datetime | None = None
    route: str | None = None
    reason: str | None = None
    confidence: Decimal | None = None


@dataclass
class CloseTokenRow:
    token_hash: str
    conversation_id: int
    handoff_started_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


@dataclass
class FakeDb:
    """ตารางทั้งหมดในหน่วยความจำ (key = id)"""

    clock: FakeClock
    users: dict[int, User] = field(default_factory=dict)
    consents: list[dict[str, Any]] = field(default_factory=list)
    raw_events: dict[int, RawEvent] = field(default_factory=dict)
    conversations: dict[int, Conversation] = field(default_factory=dict)
    states: dict[int, StateRecord] = field(default_factory=dict)
    messages: dict[int, MessageRow] = field(default_factory=dict)
    attachments: dict[int, dict[str, Any]] = field(default_factory=dict)
    ai_runs: dict[int, AiRunRow] = field(default_factory=dict)
    ai_steps: list[AiStepRecord] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    close_tokens: dict[str, CloseTokenRow] = field(default_factory=dict)
    flags: dict[str, str] = field(default_factory=dict)
    _ids: dict[str, itertools.count[int]] = field(default_factory=dict)

    def next_id(self, table: str) -> int:
        return next(self._ids.setdefault(table, itertools.count(1)))

    def now(self) -> datetime:
        return self.clock.now()


# --- repositories ---


class FakeRawEventsRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def insert(
        self,
        *,
        webhook_event_id: str,
        line_user_id: str | None,
        event_type: str,
        payload: JsonDict,
        occurred_at: datetime,
    ) -> int | None:
        if "replyToken" in payload:
            raise ValueError("payload ต้องตัด replyToken ออก (CHECK line_raw_events_no_reply_token)")
        if any(r.webhook_event_id == webhook_event_id for r in self.db.raw_events.values()):
            return None
        rid = self.db.next_id("raw_events")
        self.db.raw_events[rid] = RawEvent(
            id=rid,
            webhook_event_id=webhook_event_id,
            line_user_id=line_user_id,
            event_type=event_type,
            payload=dict(payload),
            occurred_at=occurred_at,
            received_at=self.db.now(),
            process_status=ProcessStatus.PENDING,
        )
        return rid

    async def get(self, raw_event_id: int) -> RawEvent | None:
        return self.db.raw_events.get(raw_event_id)

    async def claim(self, raw_event_id: int) -> RawEvent | None:
        row = self.db.raw_events.get(raw_event_id)
        if row is None or row.process_status is not ProcessStatus.PENDING:
            return None
        row = row.model_copy(
            update={
                "process_status": ProcessStatus.PROCESSING,
                "claimed_at": self.db.now(),
                "attempts": row.attempts + 1,
            }
        )
        self.db.raw_events[raw_event_id] = row
        return row

    def _update(self, raw_event_id: int, **values: Any) -> None:
        row = self.db.raw_events.get(raw_event_id)
        if row is not None:
            self.db.raw_events[raw_event_id] = row.model_copy(update=values)

    async def mark_done(self, raw_event_id: int, status: ProcessStatus = ProcessStatus.DONE) -> None:
        if status not in (ProcessStatus.DONE, ProcessStatus.SKIPPED):
            raise ValueError("mark_done รับแค่ DONE หรือ SKIPPED")
        self._update(raw_event_id, process_status=status, processed_at=self.db.now(), error=None)

    async def mark_failed(self, raw_event_id: int, error: str) -> None:
        self._update(
            raw_event_id, process_status=ProcessStatus.FAILED, processed_at=self.db.now(), error=error
        )

    async def release(self, raw_event_id: int, error: str) -> None:
        row = self.db.raw_events.get(raw_event_id)
        if row is not None and row.process_status is ProcessStatus.PROCESSING:
            self._update(raw_event_id, process_status=ProcessStatus.PENDING, error=error)

    async def list_pending_ids_for_user(self, line_user_id: str) -> list[int]:
        rows = [
            r
            for r in self.db.raw_events.values()
            if r.line_user_id == line_user_id and r.process_status is ProcessStatus.PENDING
        ]
        return [r.id for r in sorted(rows, key=lambda r: (r.occurred_at, r.id))]

    async def list_stale(self, received_before: datetime, limit: int = 100) -> list[int]:
        rows = [
            r
            for r in self.db.raw_events.values()
            if r.process_status is ProcessStatus.PENDING and r.received_at < received_before
        ]
        return [r.id for r in sorted(rows, key=lambda r: (r.received_at, r.id))][:limit]

    async def release_stuck(self, claimed_before: datetime, limit: int = 100) -> list[int]:
        rows = sorted(
            (
                r
                for r in self.db.raw_events.values()
                if r.process_status is ProcessStatus.PROCESSING
                and r.claimed_at is not None
                and r.claimed_at < claimed_before
            ),
            key=lambda r: (r.claimed_at, r.id),
        )[:limit]
        for r in rows:
            self._update(r.id, process_status=ProcessStatus.PENDING)
        return [r.id for r in rows]


class FakeUsersRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def get_or_create(self, line_user_id: str, display_name: str | None = None) -> User:
        now = self.db.now()
        for uid, u in self.db.users.items():
            if u.line_user_id == line_user_id:
                update: dict[str, Any] = {"last_seen_at": now}
                if display_name is not None:
                    update["display_name"] = display_name
                u = u.model_copy(update=update)
                self.db.users[uid] = u
                return u
        uid = self.db.next_id("users")
        user = User(
            id=uid,
            line_user_id=line_user_id,
            display_name=display_name,
            created_at=now,
            last_seen_at=now,
        )
        self.db.users[uid] = user
        return user

    async def has_consent(self, user_id: int, consent_type: str) -> bool:
        rows = [c for c in self.db.consents if c["user_id"] == user_id and c["consent_type"] == consent_type]
        if not rows:
            return False
        return bool(max(rows, key=lambda c: (c["created_at"], c["id"]))["granted"])

    # ตัวช่วยของเทสต์ (ไม่อยู่ในพอร์ต)
    def add_consent(self, user_id: int, consent_type: str, granted: bool, version: str = "1") -> None:
        self.db.consents.append(
            {
                "id": self.db.next_id("consents"),
                "user_id": user_id,
                "consent_type": consent_type,
                "granted": granted,
                "version": version,
                "created_at": self.db.now(),
            }
        )


class FakeConversationsRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def _create(self, user_id: int) -> Conversation:
        cid = self.db.next_id("conversations")
        conv = Conversation(
            id=cid, user_id=user_id, status=ConversationStatus.BOT, opened_at=self.db.now()
        )
        self.db.conversations[cid] = conv
        return conv

    def _save(self, conv: Conversation) -> Conversation:
        if conv.status is ConversationStatus.HUMAN and conv.handoff_started_at is None:
            raise ValueError("CHECK conversations_human_has_started")
        if conv.closed_at is not None and conv.status is not ConversationStatus.BOT:
            raise ValueError("CHECK conversations_closed_is_bot")
        self.db.conversations[conv.id] = conv
        return conv

    async def get(self, conversation_id: int) -> Conversation | None:
        return self.db.conversations.get(conversation_id)

    async def get_or_open(self, user_id: int, idle_ttl: timedelta) -> tuple[Conversation, bool]:
        now = self.db.now()
        open_convs = [
            c for c in self.db.conversations.values() if c.user_id == user_id and c.closed_at is None
        ]
        if not open_convs:
            return self._create(user_id), False
        conv = open_convs[0]
        if conv.status is ConversationStatus.HUMAN:
            return conv, False
        last = max(
            t for t in (conv.opened_at, conv.last_user_message_at, conv.handoff_ended_at) if t is not None
        )
        if last <= now - idle_ttl:
            self._save(conv.model_copy(update={"closed_at": now}))
            return self._create(user_id), True
        return conv, False

    async def touch_user_message(self, conversation_id: int) -> None:
        conv = self.db.conversations.get(conversation_id)
        if conv is not None:
            self._save(conv.model_copy(update={"last_user_message_at": self.db.now()}))

    async def start_handoff(self, conversation_id: int, reason: str) -> Conversation | None:
        conv = self.db.conversations.get(conversation_id)
        if conv is None or conv.status is not ConversationStatus.BOT or conv.closed_at is not None:
            return None
        return self._save(
            conv.model_copy(
                update={
                    "status": ConversationStatus.HUMAN,
                    "handoff_started_at": self.db.now(),
                    "handoff_reason": reason,
                    "handoff_ended_at": None,
                    "handoff_end_reason": None,
                }
            )
        )

    async def end_handoff(
        self, conversation_id: int, reason: HandoffEndReason, expected_started_at: datetime
    ) -> Conversation | None:
        conv = self.db.conversations.get(conversation_id)
        if (
            conv is None
            or conv.status is not ConversationStatus.HUMAN
            or conv.handoff_started_at != expected_started_at
        ):
            return None
        conv = self._save(
            conv.model_copy(
                update={
                    "status": ConversationStatus.BOT,
                    "handoff_ended_at": self.db.now(),
                    "handoff_end_reason": HandoffEndReason(reason),
                }
            )
        )
        # transaction เดียวกัน: ล้าง conversation_state
        self.db.states.pop(conversation_id, None)
        return conv

    async def list_handoff_timed_out(self, ttl: timedelta, limit: int = 100) -> list[Conversation]:
        cutoff = self.db.now() - ttl
        rows = [
            c
            for c in self.db.conversations.values()
            if c.status is ConversationStatus.HUMAN
            and c.handoff_started_at is not None
            and c.handoff_started_at <= cutoff
        ]
        return sorted(rows, key=lambda c: (c.handoff_started_at, c.id))[:limit]

    async def create_close_token(
        self, conversation_id: int, handoff_started_at: datetime, ttl: timedelta
    ) -> str:
        if conversation_id not in self.db.conversations:
            raise ValueError("FK close_tokens.conversation_id")
        token = secrets.token_urlsafe(32)
        h = hash_token(token)
        self.db.close_tokens[h] = CloseTokenRow(
            token_hash=h,
            conversation_id=conversation_id,
            handoff_started_at=handoff_started_at,
            expires_at=self.db.now() + ttl,
        )
        return token

    async def consume_close_token(self, token: str) -> CloseToken | None:
        row = self.db.close_tokens.get(hash_token(token))
        now = self.db.now()
        if row is None or row.used_at is not None or row.expires_at <= now:
            return None
        row.used_at = now
        return CloseToken(conversation_id=row.conversation_id, handoff_started_at=row.handoff_started_at)


class FakeStateRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def get(self, conversation_id: int) -> StateRecord | None:
        return self.db.states.get(conversation_id)

    async def save(
        self,
        conversation_id: int,
        *,
        state: JsonDict | None,
        counters: Counters,
        expires_at: datetime,
    ) -> None:
        if conversation_id not in self.db.conversations:
            raise ValueError("FK conversation_state.conversation_id")
        self.db.states[conversation_id] = StateRecord(
            conversation_id=conversation_id, state=state, counters=counters, expires_at=expires_at
        )

    async def clear(self, conversation_id: int) -> None:
        self.db.states.pop(conversation_id, None)


class FakeMessagesRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def _by_dedupe(self, dedupe_key: str) -> MessageRow | None:
        return next((m for m in self.db.messages.values() if m.dedupe_key == dedupe_key), None)

    async def add_inbound(
        self,
        *,
        conversation_id: int,
        raw_event_id: int,
        message_type: str,
        text_redacted: str | None,
    ) -> int:
        key = f"in:{raw_event_id}"
        existing = self._by_dedupe(key)
        if existing is not None:
            return existing.id
        mid = self.db.next_id("messages")
        self.db.messages[mid] = MessageRow(
            id=mid,
            conversation_id=conversation_id,
            raw_event_id=raw_event_id,
            direction=Direction.IN,
            message_type=message_type,
            text_redacted=text_redacted,
            payload=None,
            status=None,
            drop_reason=None,
            dedupe_key=key,
            created_at=self.db.now(),
        )
        return mid

    async def recent_texts_redacted(
        self, conversation_id: int, limit: int = 4, *, exclude_raw_event_id: int | None = None
    ) -> list[ChatTurn]:
        rows = [
            m
            for m in self.db.messages.values()
            if m.conversation_id == conversation_id
            and m.text_redacted
            and (m.direction is Direction.IN or m.status is OutboundStatus.SENT)
            and not (
                exclude_raw_event_id is not None
                and m.direction is Direction.IN
                and m.raw_event_id == exclude_raw_event_id
            )
        ]
        rows.sort(key=lambda m: (m.created_at, m.id))
        tail = rows[-limit:] if limit > 0 else []
        return [ChatTurn(direction=m.direction, text_redacted=m.text_redacted or "") for m in tail]

    async def count_inbound_since(self, conversation_id: int, since: datetime) -> int:
        return sum(
            1
            for m in self.db.messages.values()
            if m.conversation_id == conversation_id
            and m.direction is Direction.IN
            and m.created_at >= since
        )

    async def reserve_outbound(
        self,
        *,
        conversation_id: int,
        raw_event_id: int | None,
        dedupe_key: str,
        outbound: OutboundMessage,
        text_redacted: str | None = None,
    ) -> OutboundReservation:
        existing = self._by_dedupe(dedupe_key)
        if existing is not None:
            return OutboundReservation(
                message_id=existing.id, already_sent=existing.status is OutboundStatus.SENT
            )
        mid = self.db.next_id("messages")
        self.db.messages[mid] = MessageRow(
            id=mid,
            conversation_id=conversation_id,
            raw_event_id=raw_event_id,
            direction=Direction.OUT,
            message_type=outbound.purpose,
            text_redacted=text_redacted,
            payload=[dict(m) for m in outbound.messages],
            status=OutboundStatus.PENDING,
            drop_reason=None,
            dedupe_key=dedupe_key,
            created_at=self.db.now(),
        )
        return OutboundReservation(message_id=mid, already_sent=False)

    async def mark_outbound(
        self, message_id: int, status: OutboundStatus, *, reason: str | None = None
    ) -> None:
        row = self.db.messages.get(message_id)
        if row is None or row.direction is not Direction.OUT:
            return
        row.status = OutboundStatus(status)
        if row.status is OutboundStatus.SENT:
            row.sent_at = self.db.now()
        elif row.status in (OutboundStatus.DROPPED, OutboundStatus.FAILED):
            row.drop_reason = reason

    async def add_attachment(
        self, *, message_id: int, storage_key: str, content_type: str, size_bytes: int
    ) -> int:
        if message_id not in self.db.messages:
            raise ValueError("FK attachments.message_id")
        for a in self.db.attachments.values():
            if a["storage_key"] == storage_key:  # idempotent ตอน retry (ON CONFLICT ใน repo จริง)
                return a["id"]
        aid = self.db.next_id("attachments")
        self.db.attachments[aid] = {
            "id": aid,
            "message_id": message_id,
            "storage_key": storage_key,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "created_at": self.db.now(),
            "deleted_at": None,
        }
        return aid

    # ตัวช่วยของเทสต์ (ไม่อยู่ในพอร์ต)
    def outbound_rows(self, conversation_id: int | None = None) -> list[MessageRow]:
        return [
            m
            for m in self.db.messages.values()
            if m.direction is Direction.OUT
            and (conversation_id is None or m.conversation_id == conversation_id)
        ]


class FakeAiRunsRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def create_run(self, *, raw_event_id: int, conversation_id: int) -> int:
        for run in self.db.ai_runs.values():
            if run.raw_event_id == raw_event_id:
                return run.id
        rid = self.db.next_id("ai_runs")
        self.db.ai_runs[rid] = AiRunRow(
            id=rid, raw_event_id=raw_event_id, conversation_id=conversation_id, started_at=self.db.now()
        )
        return rid

    async def add_step(self, step: AiStepRecord) -> None:
        if step.ai_run_id not in self.db.ai_runs:
            raise ValueError("FK ai_steps.ai_run_id")
        self.db.ai_steps.append(step)

    async def finish_run(
        self,
        ai_run_id: int,
        *,
        route: str,
        reason: str | None = None,
        confidence: Decimal | None = None,
    ) -> None:
        run = self.db.ai_runs.get(ai_run_id)
        if run is None:
            return
        run.finished_at = self.db.now()
        run.route = route
        run.reason = reason
        run.confidence = confidence


class FakeFeedbackRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def add(
        self,
        *,
        conversation_id: int,
        kind: FeedbackKind,
        value: str | None = None,
        ai_run_id: int | None = None,
    ) -> int:
        run = self.db.ai_runs.get(ai_run_id) if ai_run_id is not None else None
        if run is None or run.conversation_id != conversation_id:
            ai_run_id = None
        fid = self.db.next_id("feedback")
        self.db.feedback.append(
            {
                "id": fid,
                "conversation_id": conversation_id,
                "ai_run_id": ai_run_id,
                "kind": FeedbackKind(kind),
                "value": value,
                "created_at": self.db.now(),
            }
        )
        return fid


class FakeFlagsRepo:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def get_flag(self, key: str) -> str | None:
        return self.db.flags.get(key)

    async def set_flag(self, key: str, value: str) -> None:
        self.db.flags[key] = value


# --- services ---


def default_ai_result() -> AiResult:
    return AiResult(kind=AiKind.ANSWER, intro="คำตอบทดสอบ", latency_ms=1, attempts=1)


class FakeAiClient:
    """คืนผลตามคิว responses ทีละตัว (Exception -> raise); หมดคิว -> handler หรือ default_ai_result()"""

    def __init__(
        self,
        *responses: AiResult | Exception,
        handler: Callable[[AiRequest], AiResult] | None = None,
    ) -> None:
        self.responses: list[AiResult | Exception] = list(responses)
        self.handler = handler
        self.calls: list[tuple[AiRequest, float]] = []

    async def call(self, req: AiRequest, deadline_s: float) -> AiResult:
        self.calls.append((req, deadline_s))
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        if self.handler is not None:
            return self.handler(req)
        return default_ai_result()


class FakeLineClient:
    """บันทึก reply/push; ใส่ Exception ใน reply_errors / push_errors เพื่อให้ครั้งถัดไปพัง"""

    def __init__(self) -> None:
        self.replies: list[tuple[str, list[JsonDict]]] = []
        self.pushes: list[tuple[str, list[JsonDict], str | None]] = []
        self.loadings: list[tuple[str, int]] = []
        self.contents: dict[str, MediaContent] = {}
        self.reply_errors: list[Exception] = []
        self.push_errors: list[Exception] = []

    async def reply(self, reply_token: str, messages: list[JsonDict]) -> None:
        if self.reply_errors:
            raise self.reply_errors.pop(0)
        self.replies.append((reply_token, list(messages)))

    async def push(
        self, line_user_id: str, messages: list[JsonDict], *, retry_key: str | None = None
    ) -> None:
        if self.push_errors:
            raise self.push_errors.pop(0)
        self.pushes.append((line_user_id, list(messages), retry_key))

    async def start_loading(self, line_user_id: str, seconds: int = 20) -> None:
        self.loadings.append((line_user_id, seconds))

    async def get_content(self, line_message_id: str) -> MediaContent:
        try:
            return self.contents[line_message_id]
        except KeyError:
            raise LineApiError("not found", status_code=404, retryable=False) from None

    # ตัวช่วยของเทสต์
    @property
    def sent(self) -> list[list[JsonDict]]:
        """ข้อความทุกชุดที่ส่งออก (reply และ push) ตามลำดับที่บันทึก"""
        return [m for _, m in self.replies] + [m for _, m, _ in self.pushes]


class FakeReplyTokens:
    """Redis replytoken:{raw_event_id} พร้อม TTL ตาม clock; take = GETDEL"""

    def __init__(self, clock: FakeClock, ttl_s: int = 50) -> None:
        self.clock = clock
        self.ttl = timedelta(seconds=ttl_s)
        self._store: dict[int, tuple[str, datetime]] = {}

    async def put(self, raw_event_id: int, reply_token: str) -> None:
        self._store[raw_event_id] = (reply_token, self.clock.now() + self.ttl)

    async def take(self, raw_event_id: int) -> str | None:
        item = self._store.pop(raw_event_id, None)
        if item is None:
            return None
        token, expires_at = item
        return token if self.clock.now() < expires_at else None


class FakeNotifier:
    def __init__(self) -> None:
        self.started: list[tuple[Conversation, str, str | None]] = []
        self.ended: list[tuple[Conversation, HandoffEndReason]] = []

    async def handoff_started(self, conv: Conversation, summary: str, close_url: str | None) -> None:
        self.started.append((conv, summary, close_url))

    async def handoff_ended(self, conv: Conversation, reason: HandoffEndReason) -> None:
        self.ended.append((conv, reason))


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_image(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeKillSwitch:
    def __init__(self, on: bool = False) -> None:
        self.on = on

    async def is_on(self) -> bool:
        return self.on


# --- ตัวประกอบ ---


def make_settings(**overrides: Any) -> Settings:
    """Settings ที่มีค่าบังคับครบ ไม่อ่าน .env (ค่าอื่นเป็นค่าตั้งต้น เว้นแต่ส่ง overrides)"""
    values: dict[str, Any] = {
        "line_channel_secret": "test-channel-secret",
        "line_channel_access_token": "test-access-token",
        "database_url": "postgresql://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "public_base_url": "https://bot.example.test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def make_deps(
    *,
    clock: FakeClock | None = None,
    settings: Settings | None = None,
    db: FakeDb | None = None,
    **overrides: Any,
) -> Deps:
    """Deps ที่ทุกพอร์ตเป็น fake ใช้ FakeDb/FakeClock ร่วมกัน; overrides แทนฟิลด์ของ Deps ได้ เช่น ai=FakeAiClient(...)

    เข้าถึง FakeDb ได้ที่ deps.raw_events.db (repository fake ทุกตัวถือ db ก้อนเดียวกัน)
    """
    clock = clock or (db.clock if db is not None else FakeClock())
    db = db or FakeDb(clock=clock)
    settings = settings or make_settings()
    fields: dict[str, Any] = {
        "settings": settings,
        "clock": clock,
        "raw_events": FakeRawEventsRepo(db),
        "users": FakeUsersRepo(db),
        "conversations": FakeConversationsRepo(db),
        "state": FakeStateRepo(db),
        "messages": FakeMessagesRepo(db),
        "ai_runs": FakeAiRunsRepo(db),
        "feedback": FakeFeedbackRepo(db),
        "flags": FakeFlagsRepo(db),
        "ai": FakeAiClient(),
        "line": FakeLineClient(),
        "reply_tokens": FakeReplyTokens(clock, settings.reply_token_ttl_s),
        "notifier": FakeNotifier(),
        "storage": FakeStorage(),
        "kill_switch": FakeKillSwitch(),
    }
    unknown = set(overrides) - set(fields)
    if unknown:
        raise TypeError(f"make_deps: ไม่รู้จัก {sorted(unknown)}")
    fields.update(overrides)
    return Deps(**fields)


def make_event(
    *,
    text: str | None = None,
    postback: str | None = None,
    message_type: str | None = None,
    raw_event_id: int = 1,
    line_user_id: str = "U-test",
    occurred_at: datetime = DEFAULT_NOW,
    line_message_id: str | None = None,
) -> InboundEvent:
    """InboundEvent สำหรับเทสต์: ส่ง text= (ข้อความ), postback= (data) หรือ message_type= (เช่น "image")"""
    if postback is not None:
        event_type = EventType.POSTBACK
        message_type = None
    else:
        event_type = EventType.MESSAGE
        message_type = message_type or "text"
    return InboundEvent(
        raw_event_id=raw_event_id,
        webhook_event_id=f"wh-{raw_event_id}",
        line_user_id=line_user_id,
        event_type=event_type,
        message_type=message_type,
        text=text if event_type is EventType.MESSAGE and message_type == "text" else None,
        postback_data=postback,
        postback_params=parse_postback_data(postback),
        line_message_id=line_message_id or (f"m-{raw_event_id}" if postback is None else None),
        occurred_at=occurred_at,
    )

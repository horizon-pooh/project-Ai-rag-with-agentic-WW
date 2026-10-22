"""tests/pipeline/test_orchestrator.py — run_turn ทั้งสายด้วย fake (app/pipeline/orchestrator.py)

 - ใช้ไฟล์กฎชั่วคราวของเทสต์เอง (rules_dir) ไม่ขึ้นกับรายการคำจริงใน prompts/rules
 - AI เป็น FakeAiClient ที่ตอบตามคิว หรือ client ในไฟล์นี้ที่เปลี่ยนสถานะระหว่างรอ (escalationRace)
 - event สร้างเป็นแถว line_raw_events จริงใน FakeDb แล้ว claim ก่อนเรียก run_turn (เหมือน worker)
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.pipeline import orchestrator, replies
from app.pipeline.orchestrator import (
    PipelineConfig,
    TurnOutcome,
    TurnRoute,
    is_fast_path_event,
    load_pipeline_config,
    run_turn,
)
from app.pipeline.ports import Deps
from app.pipeline.schemas import (
    AiBadResponse,
    AiKind,
    AiRequest,
    AiResult,
    AiUnavailable,
    Conversation,
    ConversationStatus,
    Direction,
    FeedbackKind,
    HandoffEndReason,
    JsonDict,
    OutboundStatus,
    Product,
    RawEvent,
    SendStatus,
)
from app.services import flex_builder
from tests.fakes import (
    FakeAiClient,
    FakeClock,
    FakeKillSwitch,
    FakeLineClient,
    make_deps,
    make_event,
    make_settings,
)

USER = "U-test"

P1 = Product(
    sku="RF-100",
    name="ตู้เย็น 2 ประตู",
    price=Decimal("12990"),
    url="https://shop.example/p/rf-100",
    image_url="https://shop.example/i/rf-100.jpg",
)
P2 = Product(sku="RF-200", name="ตู้เย็น 1 ประตู", url="https://shop.example/p/rf-200")

STEPS_AI_ANSWER = [
    "prepare",
    "handoff_check",
    "rules",
    "call_ai",
    "route_ai_result",
    "update_state",
    "validate",
    "send",
    "save_state",
]


# --- ตัวช่วย ---


@pytest.fixture(scope="module")
def rules_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("rules")
    (d / "escalate.txt").write_text("ร้องเรียน\nคุยกับพนักงาน\n", encoding="utf-8")
    (d / "faq.txt").write_text(
        "สวัสดี\tสวัสดีค่ะ ต้องการให้ช่วยหาสินค้าอะไรคะ\nโปรโมชั่น\t-\n", encoding="utf-8"
    )
    (d / "resume_ai.txt").write_text("คุยกับai\n", encoding="utf-8")
    return d


@pytest.fixture
def settings(rules_dir: Path):
    return make_settings(rules_dir=rules_dir)


@pytest.fixture
def cfg(settings) -> PipelineConfig:
    return load_pipeline_config(settings)


def products_result(**kw: Any) -> AiResult:
    values: dict[str, Any] = {
        "kind": AiKind.PRODUCTS,
        "intro": "แนะนำตู้เย็นค่ะ",
        "products": [P1, P2],
        "state_out": {"category": "ตู้เย็น"},
        "raw": '{"kind": "products"}',
        "latency_ms": 120,
        "attempts": 1,
    }
    values.update(kw)
    return AiResult(**values)


def clarify_result(question: str = "ต้องการสีอะไรคะ") -> AiResult:
    return AiResult(kind=AiKind.CLARIFY, clarify_question=question)


def bad_products_result() -> AiResult:
    # ลิงก์ http (ไม่ใช่ https) -> validate ไม่ผ่าน
    return AiResult(
        kind=AiKind.PRODUCTS,
        intro="แนะนำค่ะ",
        products=[Product(sku="BAD-1", name="สินค้า", url="http://insecure.example/x")],
        state_out={"category": "ปลอม"},
    )


def _deps(settings, *responses: AiResult | Exception, **kw: Any) -> Deps:
    return make_deps(settings=settings, ai=FakeAiClient(*responses), **kw)


def _db(deps: Deps):
    return deps.raw_events.db  # type: ignore[attr-defined]


async def _insert_raw(deps: Deps, payload: JsonDict, *, reply_token: bool = True) -> RawEvent:
    rid = await deps.raw_events.insert(
        webhook_event_id=payload["webhookEventId"],
        line_user_id=(payload.get("source") or {}).get("userId"),
        event_type=payload["type"],
        payload=payload,
        occurred_at=deps.clock.now(),
    )
    assert rid is not None
    if reply_token:
        await deps.reply_tokens.put(rid, f"rt-{rid}")
    raw = await deps.raw_events.claim(rid)
    assert raw is not None
    return raw


def _payload(deps: Deps, event_type: str, **extra: Any) -> JsonDict:
    return {
        "type": event_type,
        "webhookEventId": uuid.uuid4().hex,
        "timestamp": int(deps.clock.now().timestamp() * 1000),
        "mode": "active",
        "source": {"type": "user", "userId": USER},
        **extra,
    }


async def _raw(
    deps: Deps,
    *,
    text: str | None = None,
    postback: str | None = None,
    message_type: str = "text",
    reply_token: bool = True,
) -> RawEvent:
    if postback is not None:
        payload = _payload(deps, "postback", postback={"data": postback})
    else:
        message: JsonDict = {"type": message_type, "id": f"m-{uuid.uuid4().hex[:8]}"}
        if message_type == "text":
            message["text"] = text or ""
        payload = _payload(deps, "message", message=message)
    return await _insert_raw(deps, payload, reply_token=reply_token)


async def _turn(deps: Deps, cfg: PipelineConfig, **kw: Any) -> TurnOutcome:
    return await run_turn(deps, await _raw(deps, **kw), cfg)


def _conv(deps: Deps) -> Conversation:
    (conv,) = [c for c in _db(deps).conversations.values() if c.closed_at is None]
    return conv


def _last_sent(deps: Deps) -> list[JsonDict]:
    return deps.line.sent[-1]  # type: ignore[attr-defined]


def _steps(deps: Deps, ai_run_id: int | None) -> list[str]:
    return [s.step for s in _db(deps).ai_steps if s.ai_run_id == ai_run_id]


def _step(deps: Deps, ai_run_id: int | None, name: str):
    (step,) = [s for s in _db(deps).ai_steps if s.ai_run_id == ai_run_id and s.step == name]
    return step


def _ai_calls(deps: Deps) -> list[tuple[AiRequest, float]]:
    return deps.ai.calls  # type: ignore[attr-defined]


def _text(t: str) -> JsonDict:
    return replies.text_message(t)


# --- เส้นทาง AI ปกติ ---


async def test_happy_path_products_carousel_state_and_steps(settings, cfg) -> None:
    deps = _deps(settings, products_result())
    raw = await _raw(deps, text="หาตู้เย็น 2 ประตู โทร 0812345678")
    out = await run_turn(deps, raw, cfg)

    assert out.route == TurnRoute.AI_ANSWER and out.reason == "products"
    assert out.sent == [SendStatus.SENT]

    # AI เห็นแค่ข้อความที่ redact แล้ว และไม่ได้รับรูป
    ((req, deadline),) = _ai_calls(deps)
    assert req.text_redacted == "หาตู้เย็น 2 ประตู โทร [PHONE]"
    assert req.state is None and req.images == [] and req.recent_messages == []
    assert 0 < deadline <= settings.turn_deadline_s
    assert deps.line.loadings == [(USER, 50)]  # type: ignore[attr-defined]

    # reply ด้วย replyToken ของ event นี้: intro + carousel ที่สร้างจาก products
    ((token, msgs),) = deps.line.replies  # type: ignore[attr-defined]
    assert token == f"rt-{raw.id}"
    assert msgs[0] == _text("แนะนำตู้เย็นค่ะ")
    assert msgs[1] == flex_builder.product_carousel([P1, P2])

    # state + counters บันทึกแล้ว ต่ออายุ STATE_TTL
    conv = _conv(deps)
    rec = _db(deps).states[conv.id]
    assert rec.state == {"category": "ตู้เย็น"}
    assert rec.counters.last_products == [P1, P2]
    assert rec.expires_at == deps.clock.now() + settings.state_ttl

    # ai_steps ทุกขั้น เรียง seq ติด id ครบ
    steps = [s for s in _db(deps).ai_steps if s.ai_run_id == out.ai_run_id]
    assert [s.step for s in steps] == STEPS_AI_ANSWER
    assert [s.seq for s in steps] == list(range(1, len(STEPS_AI_ANSWER) + 1))
    assert all(s.raw_event_id == raw.id for s in steps)
    call = _step(deps, out.ai_run_id, "call_ai")
    assert call.latency_ms == 120 and call.attempts == 1 and call.error_class is None
    assert call.output is not None and call.output["raw"] == '{"kind": "products"}'
    upd = _step(deps, out.ai_run_id, "update_state")
    assert upd.state_before is None and upd.state_after == {"category": "ตู้เย็น"}
    # เบอร์โทรดิบไม่อยู่ใน trace และไม่อยู่ใน messages
    assert "0812345678" not in repr(_db(deps).ai_steps)
    assert all("0812345678" not in (m.text_redacted or "") for m in _db(deps).messages.values())

    run = _db(deps).ai_runs[out.ai_run_id]
    assert run.route == "ai_answer" and run.finished_at is not None

    (row,) = deps.messages.outbound_rows()  # type: ignore[attr-defined]
    assert row.status is OutboundStatus.SENT and row.message_type == "ai_answer"
    assert row.dedupe_key == f"out:{raw.id}:1"
    assert row.text_redacted is not None and "แนะนำตู้เย็นค่ะ" in row.text_redacted
    assert flex_builder.carousel_alt_text([P1, P2]) in row.text_redacted


async def test_next_turn_sends_state_and_history_to_ai(settings, cfg) -> None:
    deps = _deps(settings, products_result(), products_result(state_out={"category": "ตู้เย็น", "color": "เขียว"}))
    await _turn(deps, cfg, text="ตู้เย็น")
    deps.clock.advance(seconds=30)  # type: ignore[attr-defined]
    await _turn(deps, cfg, text="เขียว")
    req, _ = _ai_calls(deps)[1]
    assert req.state == {"category": "ตู้เย็น"}
    assert req.flags.state_expired is False
    assert [t.direction for t in req.recent_messages] == [Direction.IN, Direction.OUT]
    assert req.recent_messages[0].text_redacted == "ตู้เย็น"
    assert _db(deps).states[_conv(deps).id].state == {"category": "ตู้เย็น", "color": "เขียว"}


async def test_cannot_answer_offers_staff_then_escalates(settings, cfg) -> None:
    deps = _deps(settings, AiResult(kind=AiKind.CANNOT_ANSWER), AiResult(kind=AiKind.CANNOT_ANSWER))
    o1 = await _turn(deps, cfg, text="มีบริการติดตั้งที่เชียงรายไหม")
    assert o1.route == TurnRoute.AI_OFFER_STAFF
    assert _last_sent(deps) == [replies.cannot_answer_message()]
    assert _db(deps).states[_conv(deps).id].counters.cannot_answer_streak == 1

    o2 = await _turn(deps, cfg, text="แล้วที่เชียงใหม่ล่ะ")
    assert o2.route == TurnRoute.AI_ESCALATE and o2.reason == "ai:cannot_answer_repeat"
    assert _conv(deps).status is ConversationStatus.HUMAN
    assert _last_sent(deps) == [_text(replies.HANDOFF_IN_HOURS)]


async def test_escalate_hint_escalates(settings, cfg) -> None:
    deps = _deps(settings, AiResult(kind=AiKind.ANSWER, intro="ได้ค่ะ", escalate_hint=True))
    out = await _turn(deps, cfg, text="ของที่ส่งมาไม่ครบ")
    assert out.route == TurnRoute.AI_ESCALATE and out.reason == "ai:escalate_hint"
    conv = _conv(deps)
    assert conv.status is ConversationStatus.HUMAN and conv.handoff_reason == "ai:escalate_hint"
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]


# --- rules ---


async def test_faq_reply_without_ai_call(settings, cfg) -> None:
    deps = _deps(settings)
    out = await _turn(deps, cfg, text="สวัสดี")
    assert out.route == TurnRoute.RULE_REPLY and out.reason == "faq"
    assert out.sent == [SendStatus.SENT]
    assert _ai_calls(deps) == [] and deps.line.loadings == []  # type: ignore[attr-defined]
    assert _last_sent(deps) == [_text("สวัสดีค่ะ ต้องการให้ช่วยหาสินค้าอะไรคะ")]
    assert _steps(deps, out.ai_run_id) == ["prepare", "handoff_check", "rules", "send"]


async def test_vendor1_keyword_is_ignored(settings, cfg) -> None:
    deps = _deps(settings)
    out = await _turn(deps, cfg, text="โปรโมชั่น")
    assert out.route == TurnRoute.RULE_IGNORE and out.reason == "vendor1"
    assert out.sent == [] and deps.line.sent == [] and _ai_calls(deps) == []  # type: ignore[attr-defined]


async def test_rule_escalate_sets_human_notifies_once_and_sends_handoff_text(settings, cfg) -> None:
    deps = _deps(settings)
    await _turn(deps, cfg, text="ตู้เย็น")  # รอบก่อนหน้า (เข้า history ของสรุป)
    out = await _turn(deps, cfg, text="อยากร้องเรียนค่ะ เบอร์ 0812345678")

    assert out.route == TurnRoute.RULE_ESCALATE and out.reason == "rule:ร้องเรียน"
    assert out.sent == [SendStatus.SENT]
    conv = _conv(deps)
    assert conv.status is ConversationStatus.HUMAN and conv.handoff_reason == "rule:ร้องเรียน"
    ((notified, summary, close_url),) = deps.notifier.started  # type: ignore[attr-defined]
    assert notified.id == conv.id
    assert "ลูกค้า: ตู้เย็น" in summary
    assert "ลูกค้า: อยากร้องเรียนค่ะ เบอร์ [PHONE]" in summary
    assert "0812345678" not in summary
    assert close_url is not None and close_url.startswith("https://bot.example.test/close?token=")
    assert _last_sent(deps) == [_text(replies.HANDOFF_IN_HOURS)]
    assert len(_ai_calls(deps)) == 1  # เฉพาะรอบแรก รอบร้องเรียนไม่เรียก AI
    assert _steps(deps, out.ai_run_id) == ["prepare", "handoff_check", "rules", "handoff", "send"]


async def test_talk_to_staff_postback_out_of_hours(rules_dir, cfg) -> None:
    settings = make_settings(rules_dir=rules_dir, callcenter_hours="13:00-18:00")  # ตอนนี้ 10:00 ไทย
    deps = _deps(settings)
    out = await _turn(deps, cfg, postback="action=talk_to_staff")
    assert out.route == TurnRoute.USER_ESCALATE and out.reason == "user:talk_to_staff"
    assert _last_sent(deps) == [_text(replies.HANDOFF_OUT_OF_HOURS.format(hours="13:00-18:00"))]
    assert "[กดปุ่ม talk_to_staff]" in deps.notifier.started[0][1]  # type: ignore[attr-defined]


async def test_human_is_silent_and_message_stored(settings, cfg) -> None:
    deps = _deps(settings)
    await _turn(deps, cfg, text="ร้องเรียน")
    sent_before = len(deps.line.sent)  # type: ignore[attr-defined]
    out = await _turn(deps, cfg, text="ยังรออยู่นะคะ")
    assert out.route == TurnRoute.SILENT and out.sent == []
    assert len(deps.line.sent) == sent_before  # type: ignore[attr-defined]
    assert _ai_calls(deps) == []
    inbound = [m.text_redacted for m in _db(deps).messages.values() if m.direction is Direction.IN]
    assert inbound[-1] == "ยังรออยู่นะคะ"
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]


async def test_empty_and_image_only_messages_reply_empty(settings, cfg) -> None:
    deps = _deps(settings)
    o1 = await _turn(deps, cfg, message_type="sticker")
    assert o1.route == TurnRoute.RULE_EMPTY
    assert _last_sent(deps) == [_text(replies.EMPTY)]
    # รอคำตอบ: AI รับรูปได้ไหม — ตอนนี้รูปเดี่ยวๆ ไม่ส่ง AI นับเป็นข้อความว่าง
    o2 = await _turn(deps, cfg, message_type="image")
    assert o2.route == TurnRoute.RULE_EMPTY
    assert _ai_calls(deps) == []


async def test_rate_limit_replies_only_once(rules_dir) -> None:
    settings = make_settings(rules_dir=rules_dir, rate_limit_per_min=2)
    cfg = load_pipeline_config(settings)
    deps = _deps(settings)
    outs = [await _turn(deps, cfg, text="สวัสดี") for _ in range(4)]
    assert [o.route for o in outs] == ["rule_reply", "rule_reply", "rate_limited", "rate_limited"]
    assert [o.sent for o in outs] == [[SendStatus.SENT]] * 3 + [[]]
    assert _last_sent(deps) == [_text(replies.RATE_LIMITED)]
    # เลยหนึ่งนาทีไป -> กลับมาตอบปกติ
    deps.clock.advance(minutes=1, seconds=1)  # type: ignore[attr-defined]
    assert (await _turn(deps, cfg, text="สวัสดี")).route == TurnRoute.RULE_REPLY


async def test_product_detail_postback(settings, cfg) -> None:
    deps = _deps(settings, products_result())
    await _turn(deps, cfg, text="ตู้เย็น")
    out = await _turn(deps, cfg, postback="action=detail&sku=RF-100")
    assert out.route == TurnRoute.PRODUCT_DETAIL and out.reason is None
    assert _last_sent(deps) == [flex_builder.product_detail_message(P1)]
    assert len(_ai_calls(deps)) == 1

    missing = await _turn(deps, cfg, postback="action=detail&sku=GONE-1")
    assert missing.reason == "not_found"
    assert _last_sent(deps) == [_text(replies.DETAIL_NOT_FOUND)]


async def test_restart_clears_state(settings, cfg) -> None:
    deps = _deps(settings, products_result())
    await _turn(deps, cfg, text="ตู้เย็น")
    conv_id = _conv(deps).id
    assert conv_id in _db(deps).states
    out = await _turn(deps, cfg, postback="action=restart")
    assert out.route == TurnRoute.RESTART
    assert conv_id not in _db(deps).states
    assert _last_sent(deps) == [_text(replies.RESTARTED)]
    step = _step(deps, out.ai_run_id, "restart")
    assert step.state_before == {"category": "ตู้เย็น"} and step.state_after is None


async def test_feedback_postback_records_without_reply(settings, cfg) -> None:
    deps = _deps(settings)
    first = await _turn(deps, cfg, text="ตู้เย็น")
    sent_before = len(deps.line.sent)  # type: ignore[attr-defined]

    down = await _turn(deps, cfg, postback=f"action=feedback&v=down&run={first.ai_run_id}")
    assert down.route == TurnRoute.FEEDBACK and down.reason == "thumb_down" and down.sent == []
    up = await _turn(deps, cfg, postback="action=feedback&v=up&run=abc")
    assert up.reason == "thumb_up"
    bad = await _turn(deps, cfg, postback="action=feedback&v=meh")
    assert bad.reason == "invalid_feedback"

    rows = _db(deps).feedback
    assert [(r["kind"], r["ai_run_id"]) for r in rows] == [
        (FeedbackKind.THUMB_DOWN, first.ai_run_id),
        (FeedbackKind.THUMB_UP, None),
    ]
    assert len(deps.line.sent) == sent_before  # type: ignore[attr-defined]


# --- AI ล่ม / deadline ---


async def test_ai_unavailable_not_risky_apologizes_with_staff_button(settings, cfg) -> None:
    err = AiUnavailable("timeout", error_class="timeout", attempts=2, latency_ms=30000)
    deps = _deps(settings, err)
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.route == TurnRoute.AI_DOWN and out.reason == "timeout"
    assert _last_sent(deps) == [replies.ai_down_message()]
    assert _conv(deps).status is ConversationStatus.BOT
    call = _step(deps, out.ai_run_id, "call_ai")
    assert call.error_class == "timeout" and call.attempts == 2 and call.latency_ms == 30000
    assert _step(deps, out.ai_run_id, "fallback").error_class == "timeout"


async def test_ai_bad_response_risky_escalates(settings, cfg, monkeypatch) -> None:
    # ข้อความเสี่ยงตามกฎขั้น 5 ถูก rules ส่งต่อก่อนถึง AI เสมอ (กฎชุดเดียวกัน) — ทาง risky ของ fallback
    # เป็นตาข่ายกันพลาด จึงจำลอง is_risky ให้ True เพื่อทดสอบทางนี้
    monkeypatch.setattr(orchestrator, "is_risky", lambda text, rules: True)
    deps = _deps(settings, AiBadResponse("bad json", error_class="bad_json"))
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.route == TurnRoute.AI_DOWN_ESCALATE and out.reason == "bad_json"
    conv = _conv(deps)
    assert conv.status is ConversationStatus.HUMAN and conv.handoff_reason == "ai_down:bad_json"
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]
    assert _last_sent(deps) == [_text(replies.HANDOFF_IN_HOURS)]


class _SlowLoadingLine(FakeLineClient):
    """loading animation ที่กินเวลา 60 วิ (เกิน TURN_DEADLINE_S และอายุ replyToken)"""

    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock

    async def start_loading(self, line_user_id: str, seconds: int = 20) -> None:
        await super().start_loading(line_user_id, seconds)
        self.clock.advance(seconds=60)


async def test_deadline_spent_goes_to_fallback_without_calling_ai(settings, cfg) -> None:
    clock = FakeClock()
    deps = _deps(settings, clock=clock, line=_SlowLoadingLine(clock))
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.route == TurnRoute.AI_DOWN and out.reason == "deadline"
    assert _ai_calls(deps) == []
    assert _step(deps, out.ai_run_id, "call_ai").attempts == 0
    # replyToken หมดอายุแล้ว -> push
    assert deps.line.replies == []  # type: ignore[attr-defined]
    assert deps.line.pushes[0][1] == [replies.ai_down_message()]  # type: ignore[attr-defined]


class _BrokenLoadingLine(FakeLineClient):
    async def start_loading(self, line_user_id: str, seconds: int = 20) -> None:
        raise RuntimeError("loading พัง")


async def test_loading_failure_is_ignored(settings, cfg) -> None:
    deps = _deps(settings, products_result(), line=_BrokenLoadingLine())
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.route == TurnRoute.AI_ANSWER and out.sent == [SendStatus.SENT]


# --- validate / ตัวนับ ---


async def test_validate_fail_retry_then_escalate(settings, cfg) -> None:
    deps = _deps(settings, bad_products_result(), bad_products_result())
    o1 = await _turn(deps, cfg, text="ตู้เย็น")
    assert o1.route == TurnRoute.VALIDATE_RETRY
    assert _last_sent(deps) == [_text(replies.VALIDATE_RETRY)]
    rec = _db(deps).states[_conv(deps).id]
    assert rec.counters.validate_fail_streak == 1
    # ผลที่ไม่ผ่าน validate ไม่ถูกใช้: state_out / products ไม่เข้า state
    assert rec.state is None and rec.counters.last_products == []

    o2 = await _turn(deps, cfg, text="ตู้เย็นสีขาว")
    assert o2.route == TurnRoute.VALIDATE_ESCALATE and o2.reason == "validate:repeat_fail"
    assert _conv(deps).status is ConversationStatus.HUMAN
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]
    assert _last_sent(deps) == [_text(replies.HANDOFF_IN_HOURS)]


async def test_validate_ok_resets_fail_streak(settings, cfg) -> None:
    deps = _deps(settings, bad_products_result(), products_result())
    await _turn(deps, cfg, text="ตู้เย็น")
    out = await _turn(deps, cfg, text="ตู้เย็น 2 ประตู")
    assert out.route == TurnRoute.AI_ANSWER
    assert _db(deps).states[_conv(deps).id].counters.validate_fail_streak == 0


async def test_clarify_streak_over_max_escalates(settings, cfg) -> None:
    assert settings.clarify_max == 2
    deps = _deps(settings, clarify_result(), clarify_result(), clarify_result())
    o1 = await _turn(deps, cfg, text="อยากได้ของ")
    o2 = await _turn(deps, cfg, text="ของใช้")
    assert [o1.route, o2.route] == [TurnRoute.AI_CLARIFY, TurnRoute.AI_CLARIFY]
    assert _last_sent(deps) == [_text("ต้องการสีอะไรคะ")]
    assert _db(deps).states[_conv(deps).id].counters.clarify_streak == 2

    o3 = await _turn(deps, cfg, text="ไม่รู้สิ")
    assert o3.route == TurnRoute.AI_ESCALATE and o3.reason == "ai:clarify_repeat"
    assert _conv(deps).status is ConversationStatus.HUMAN


# --- ความจำหมดอายุ / ส่งต่อแล้วกลับมา ---


async def test_state_expired_clarify_gets_prefix_and_restart_button(settings, cfg) -> None:
    deps = _deps(settings, products_result(), clarify_result("ต้องการสินค้าสีเขียวประเภทไหนคะ"))
    await _turn(deps, cfg, text="ตู้เย็น")
    deps.clock.advance(settings.state_ttl + timedelta(minutes=1))  # type: ignore[attr-defined]

    out = await _turn(deps, cfg, text="เขียว")
    assert out.route == TurnRoute.AI_CLARIFY
    req, _ = _ai_calls(deps)[1]
    assert req.state is None and req.flags.state_expired is True
    msgs = _last_sent(deps)
    assert msgs[0] == _text(replies.STATE_EXPIRED)
    assert msgs[1]["text"] == "ต้องการสินค้าสีเขียวประเภทไหนคะ"
    # ปุ่ม "เริ่มใหม่" อยู่ที่ข้อความสุดท้าย (LINE แสดง quick reply เฉพาะข้อความสุดท้าย)
    assert msgs[1]["quickReply"] == replies.state_expired_message()["quickReply"]
    assert len(msgs) == 2


async def test_state_expired_answer_has_no_expired_prefix(settings, cfg) -> None:
    deps = _deps(settings, products_result(), products_result())
    await _turn(deps, cfg, text="ตู้เย็น")
    deps.clock.advance(settings.state_ttl + timedelta(minutes=1))  # type: ignore[attr-defined]
    out = await _turn(deps, cfg, text="ตู้เย็น 2 ประตู")
    assert out.route == TurnRoute.AI_ANSWER
    assert _ai_calls(deps)[1][0].flags.state_expired is True
    assert _last_sent(deps)[0] == _text("แนะนำตู้เย็นค่ะ")


async def test_resume_command_while_human_resumes_and_clears_state(settings, cfg) -> None:
    deps = _deps(settings, products_result())
    await _turn(deps, cfg, text="ตู้เย็น")
    conv_id = _conv(deps).id
    await _turn(deps, cfg, text="ร้องเรียน")
    assert conv_id in _db(deps).states  # ส่งต่อไม่ล้าง state; คืน AI ล้าง

    out = await _turn(deps, cfg, text="คุยกับ AI")
    assert out.route == TurnRoute.RESUMED and out.sent == [SendStatus.SENT]
    conv = _conv(deps)
    assert conv.status is ConversationStatus.BOT
    assert conv.handoff_end_reason is HandoffEndReason.USER_RESUMED
    assert conv_id not in _db(deps).states
    assert _last_sent(deps) == [_text(replies.RESUMED)]
    assert [r for _, r in deps.notifier.ended] == [HandoffEndReason.USER_RESUMED]  # type: ignore[attr-defined]


async def test_resume_command_while_bot_says_already_bot(settings, cfg) -> None:
    deps = _deps(settings)
    out = await _turn(deps, cfg, postback="action=resume_ai")
    assert out.route == TurnRoute.ALREADY_BOT
    assert _last_sent(deps) == [_text(replies.ALREADY_BOT)]


async def test_timed_out_handoff_prefixes_next_answer(settings, cfg) -> None:
    deps = _deps(settings, products_result())
    await _turn(deps, cfg, text="ร้องเรียน")
    deps.clock.advance(settings.handoff_ttl + timedelta(minutes=1))  # type: ignore[attr-defined]

    out = await _turn(deps, cfg, text="หาตู้เย็นค่ะ")
    assert out.route == TurnRoute.AI_ANSWER
    conv = _conv(deps)
    assert conv.status is ConversationStatus.BOT
    assert conv.handoff_end_reason is HandoffEndReason.TIMEOUT
    ((req, _),) = _ai_calls(deps)
    assert req.flags.handoff_timed_out is True and req.state is None
    msgs = _last_sent(deps)
    assert msgs[0] == _text(replies.HANDOFF_TIMED_OUT_PREFIX)
    assert msgs[1] == _text("แนะนำตู้เย็นค่ะ")
    assert msgs[2]["type"] == "flex"


async def test_timed_out_handoff_prefixes_rule_reply(settings, cfg) -> None:
    deps = _deps(settings)
    await _turn(deps, cfg, text="ร้องเรียน")
    deps.clock.advance(settings.handoff_ttl + timedelta(minutes=1))  # type: ignore[attr-defined]
    out = await _turn(deps, cfg, text="สวัสดี")
    assert out.route == TurnRoute.RULE_REPLY
    assert _last_sent(deps) == [
        _text(replies.HANDOFF_TIMED_OUT_PREFIX),
        _text("สวัสดีค่ะ ต้องการให้ช่วยหาสินค้าอะไรคะ"),
    ]


# --- escalationRace / retry / kill switch ---


class _RaceAi:
    """AI ที่ระหว่างรอ มีทางอื่น (เช่น fast path) ตั้ง conversation เป็น human ไปก่อน"""

    def __init__(self, result: AiResult) -> None:
        self.result = result
        self.deps: Deps | None = None

    async def call(self, req: AiRequest, deadline_s: float) -> AiResult:
        assert self.deps is not None
        conv = _conv(self.deps)
        assert await self.deps.conversations.start_handoff(conv.id, "fast_path") is not None
        return self.result


async def test_escalation_race_drops_late_answer(settings, cfg) -> None:
    ai = _RaceAi(products_result())
    deps = make_deps(settings=settings, ai=ai)
    ai.deps = deps
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.route == TurnRoute.AI_ANSWER
    assert out.sent == [SendStatus.DROPPED_HUMAN]
    assert deps.line.sent == []  # type: ignore[attr-defined]
    (row,) = deps.messages.outbound_rows()  # type: ignore[attr-defined]
    assert row.status is OutboundStatus.DROPPED and row.drop_reason == "conversation_human"
    # ระหว่าง human ไม่เขียน state
    assert _db(deps).states == {}
    assert _step(deps, out.ai_run_id, "save_state").output == {
        "saved": False,
        "counters": {
            "clarify_streak": 0,
            "cannot_answer_streak": 0,
            "validate_fail_streak": 0,
            "last_products": ["RF-100", "RF-200"],
        },
    }


async def test_escalation_race_ai_escalate_after_human_sends_nothing(settings, cfg) -> None:
    ai = _RaceAi(AiResult(kind=AiKind.ANSWER, intro="x", escalate_hint=True))
    deps = make_deps(settings=settings, ai=ai)
    ai.deps = deps
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.route == TurnRoute.AI_ESCALATE and out.sent == []
    assert deps.notifier.started == []  # type: ignore[attr-defined]
    assert _step(deps, out.ai_run_id, "handoff").output == {"started": False}


class _FastPathDuringAi:
    """ระหว่างรอ AI ลูกค้ากด 'คุยกับเจ้าหน้าที่' -> fast path รัน run_turn ของ postback นั้นทันที (ไม่รอล็อก)"""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self.deps: Deps | None = None
        self.fast: TurnOutcome | None = None

    async def call(self, req: AiRequest, deadline_s: float) -> AiResult:
        assert self.deps is not None
        raw = await _raw(self.deps, postback="action=talk_to_staff")
        self.fast = await run_turn(self.deps, raw, self.cfg)
        return products_result()


async def test_escalation_race_with_fast_path_turn(settings, cfg) -> None:
    ai = _FastPathDuringAi(cfg)
    deps = make_deps(settings=settings, ai=ai)
    ai.deps = deps
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert ai.fast is not None
    assert ai.fast.route == TurnRoute.USER_ESCALATE and ai.fast.sent == [SendStatus.SENT]
    assert out.sent == [SendStatus.DROPPED_HUMAN]
    assert deps.line.sent == [[_text(replies.HANDOFF_IN_HOURS)]]  # type: ignore[attr-defined]
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]


async def test_retry_same_raw_does_not_send_twice(settings, cfg) -> None:
    deps = _deps(settings, products_result(), products_result())
    raw = await _raw(deps, text="ตู้เย็น")
    first = await run_turn(deps, raw, cfg)
    again = await run_turn(deps, raw, cfg)  # worker ตายก่อน mark_done แล้วคิว retry
    assert first.sent == [SendStatus.SENT]
    assert again.sent == [SendStatus.DUPLICATE]
    assert first.ai_run_id == again.ai_run_id
    assert len(deps.line.sent) == 1  # type: ignore[attr-defined]
    assert len(deps.messages.outbound_rows()) == 1  # type: ignore[attr-defined]
    assert len([m for m in _db(deps).messages.values() if m.direction is Direction.IN]) == 1


async def test_retry_with_different_ai_route_does_not_send_twice(settings, cfg) -> None:
    # retry เรียก AI ใหม่แล้วได้ clarify แทน products -> purpose ต่าง แต่ต้องไม่ส่งชุดที่สอง
    deps = _deps(settings, products_result(), clarify_result())
    raw = await _raw(deps, text="ตู้เย็น")
    first = await run_turn(deps, raw, cfg)
    again = await run_turn(deps, raw, cfg)
    assert first.sent == [SendStatus.SENT]
    assert again.sent == [SendStatus.DUPLICATE]
    assert len(deps.line.sent) == 1  # type: ignore[attr-defined]


async def test_retry_of_escalation_turn_is_silent(settings, cfg) -> None:
    deps = _deps(settings)
    raw = await _raw(deps, text="ร้องเรียน")
    await run_turn(deps, raw, cfg)
    again = await run_turn(deps, raw, cfg)
    assert again.route == TurnRoute.SILENT and again.sent == []
    assert len(deps.notifier.started) == 1  # type: ignore[attr-defined]
    assert len(deps.line.sent) == 1  # type: ignore[attr-defined]


async def test_kill_switch_drops_outbound(settings, cfg) -> None:
    deps = _deps(settings, products_result(), kill_switch=FakeKillSwitch(on=True))
    out = await _turn(deps, cfg, text="ตู้เย็น")
    assert out.sent == [SendStatus.DROPPED_KILL_SWITCH]
    assert deps.line.sent == []  # type: ignore[attr-defined]


# --- event ที่ไม่ทำ ---


async def test_follow_event_is_skipped_without_writes(settings, cfg) -> None:
    deps = _deps(settings)
    raw = await _insert_raw(deps, _payload(deps, "follow"))
    out = await run_turn(deps, raw, cfg)
    assert out.route == TurnRoute.SKIPPED_EVENT and out.reason == "follow"
    assert out.ai_run_id is None and out.sent == []
    assert _db(deps).users == {} and _db(deps).messages == {}


async def test_group_event_without_user_is_skipped(settings, cfg) -> None:
    deps = _deps(settings)
    payload = _payload(deps, "message", message={"type": "text", "id": "m1", "text": "hi"})
    payload["source"] = {"type": "group", "groupId": "G1"}
    raw = await _insert_raw(deps, payload, reply_token=False)
    out = await run_turn(deps, raw, cfg)
    assert out.route == TurnRoute.SKIPPED_EVENT and out.reason == "invalid_event"
    assert _db(deps).users == {}


# --- is_fast_path_event / load_pipeline_config ---


@pytest.mark.parametrize(
    ("event_kw", "expected"),
    [
        ({"postback": "action=talk_to_staff"}, True),
        ({"postback": "action=resume_ai"}, True),
        ({"text": "คุยกับ AI"}, True),
        ({"text": " คุย กับ ai "}, True),
        ({"text": "อยากร้องเรียนค่ะ"}, True),
        ({"text": "ขอคุยกับพนักงานหน่อย"}, True),
        ({"text": "คุยกับ AI หน่อย หาตู้เย็น"}, False),
        ({"text": "หาตู้เย็น"}, False),
        ({"text": "สวัสดี"}, False),
        ({"postback": "action=detail&sku=A1"}, False),
        ({"postback": "action=restart"}, False),
        ({"message_type": "image"}, False),
    ],
)
def test_is_fast_path_event(cfg, event_kw, expected) -> None:
    assert is_fast_path_event(make_event(**event_kw), cfg) is expected


def test_load_pipeline_config_reads_real_rule_files() -> None:
    rules_dir = Path(__file__).resolve().parents[2] / "prompts" / "rules"
    cfg = load_pipeline_config(make_settings(rules_dir=rules_dir))
    assert "ร้องเรียน" in cfg.rules.escalate
    assert "คุยกับai" in cfg.resume_phrases
    assert "สวัสดี" in cfg.rules.faq


def test_load_pipeline_config_missing_dir_fails_fast(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pipeline_config(make_settings(rules_dir=tmp_path / "nope"))

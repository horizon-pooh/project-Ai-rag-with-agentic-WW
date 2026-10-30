"""tests/pipeline/test_call_ai.py — build_ai_request / call_ai / route_ai_result (app/pipeline/steps/call_ai.py)"""

from __future__ import annotations

import pytest

from app.pipeline.schemas import (
    AiBadResponse,
    AiFlags,
    AiKind,
    AiResult,
    AiUnavailable,
    ChatTurn,
    Counters,
    Direction,
    Product,
)
from app.pipeline.steps.call_ai import AiRoute, build_ai_request, call_ai, route_ai_result
from tests.fakes import FakeAiClient, make_deps, make_settings

SETTINGS = make_settings()  # clarify_max=2


def _turn(i: int) -> ChatTurn:
    return ChatTurn(direction=Direction.IN if i % 2 else Direction.OUT, text_redacted=f"t{i}")


def _product(sku: str = "A1") -> Product:
    return Product(sku=sku, name="ตู้เย็น", url="https://shop.example/a1")


# --- build_ai_request ---


def test_build_ai_request_keeps_last_four_turns_in_order() -> None:
    req = build_ai_request(
        state={"category": "ตู้เย็น"},
        text_redacted="สีเขียว",
        history=[_turn(i) for i in range(6)],
        flags=AiFlags(just_resumed=True),
    )
    assert [t.text_redacted for t in req.recent_messages] == ["t2", "t3", "t4", "t5"]
    assert req.state == {"category": "ตู้เย็น"}
    assert req.text_redacted == "สีเขียว"
    assert req.flags.just_resumed is True
    assert req.images == []


def test_build_ai_request_state_none_and_images() -> None:
    req = build_ai_request(
        state=None,
        text_redacted="x",
        history=[],
        flags=AiFlags(state_expired=True),
        images=("img/1",),
    )
    assert req.state is None
    assert req.recent_messages == []
    assert req.images == ["img/1"]
    assert req.flags.state_expired is True


# --- call_ai ---


async def test_call_ai_passes_request_and_deadline() -> None:
    result = AiResult(kind=AiKind.ANSWER, intro="ok")
    ai = FakeAiClient(result)
    deps = make_deps(ai=ai)
    req = build_ai_request(state=None, text_redacted="x", history=[], flags=AiFlags())
    assert await call_ai(deps, req, deadline_s=12.5) is result
    assert ai.calls == [(req, 12.5)]


@pytest.mark.parametrize("deadline", [0, -1.0])
async def test_call_ai_no_time_left_raises_without_calling(deadline: float) -> None:
    ai = FakeAiClient()
    deps = make_deps(ai=ai)
    req = build_ai_request(state=None, text_redacted="x", history=[], flags=AiFlags())
    with pytest.raises(AiUnavailable) as ei:
        await call_ai(deps, req, deadline_s=deadline)
    assert ei.value.error_class == "deadline"
    assert ai.calls == []


@pytest.mark.parametrize(
    "error",
    [
        AiUnavailable("down", error_class="timeout", attempts=2),
        AiBadResponse("bad", error_class="parse"),
    ],
)
async def test_call_ai_errors_propagate(error: Exception) -> None:
    deps = make_deps(ai=FakeAiClient(error))
    req = build_ai_request(state=None, text_redacted="x", history=[], flags=AiFlags())
    with pytest.raises(type(error)):
        await call_ai(deps, req, deadline_s=10)


# --- route_ai_result ---


def test_escalate_hint_wins_over_any_kind() -> None:
    for kind in AiKind:
        route, _ = route_ai_result(AiResult(kind=kind, escalate_hint=True), Counters(), SETTINGS)
        assert route is AiRoute.ESCALATE


def test_escalate_hint_none_or_false_does_not_escalate() -> None:
    for hint in (None, False):
        route, _ = route_ai_result(
            AiResult(kind=AiKind.ANSWER, escalate_hint=hint), Counters(), SETTINGS
        )
        assert route is AiRoute.ANSWER


def test_cannot_answer_first_offers_staff_second_escalates() -> None:
    r = AiResult(kind=AiKind.CANNOT_ANSWER)
    route, c1 = route_ai_result(r, Counters(), SETTINGS)
    assert route is AiRoute.OFFER_STAFF
    assert c1.cannot_answer_streak == 1
    route, c2 = route_ai_result(r, c1, SETTINGS)
    assert route is AiRoute.ESCALATE
    assert c2.cannot_answer_streak == 0  # ส่งต่อแล้วเริ่มนับใหม่


def test_clarify_escalates_only_after_clarify_max() -> None:
    r = AiResult(kind=AiKind.CLARIFY, clarify_question="สีอะไรคะ")
    c = Counters()
    for expected in (1, 2):  # clarify_max = 2
        route, c = route_ai_result(r, c, SETTINGS)
        assert route is AiRoute.CLARIFY
        assert c.clarify_streak == expected
    route, c = route_ai_result(r, c, SETTINGS)
    assert route is AiRoute.ESCALATE
    assert c.clarify_streak == 0


def test_clarify_max_from_settings() -> None:
    r = AiResult(kind=AiKind.CLARIFY, clarify_question="?")
    route, c = route_ai_result(r, Counters(), make_settings(clarify_max=1))
    assert route is AiRoute.CLARIFY
    route, _ = route_ai_result(r, c, make_settings(clarify_max=1))
    assert route is AiRoute.ESCALATE


@pytest.mark.parametrize("kind", [AiKind.PRODUCTS, AiKind.ANSWER])
def test_answer_resets_streaks_but_keeps_other_counters(kind: AiKind) -> None:
    before = Counters(
        clarify_streak=2, cannot_answer_streak=1, validate_fail_streak=1, last_products=[_product()]
    )
    route, after = route_ai_result(AiResult(kind=kind, intro="x"), before, SETTINGS)
    assert route is AiRoute.ANSWER
    assert after.clarify_streak == 0 and after.cannot_answer_streak == 0
    assert after.validate_fail_streak == 1
    assert after.last_products == before.last_products
    # immutable: ของเดิมไม่ถูกแก้
    assert before.clarify_streak == 2 and before.cannot_answer_streak == 1


def test_streaks_are_consecutive_per_kind() -> None:
    # cannot_answer -> clarify -> cannot_answer : ไม่ติดกัน -> เสนอปุ่มอีกรอบ ไม่ส่งต่อ
    c = Counters()
    route, c = route_ai_result(AiResult(kind=AiKind.CANNOT_ANSWER), c, SETTINGS)
    assert route is AiRoute.OFFER_STAFF
    route, c = route_ai_result(AiResult(kind=AiKind.CLARIFY, clarify_question="?"), c, SETTINGS)
    assert route is AiRoute.CLARIFY and c.cannot_answer_streak == 0
    route, c = route_ai_result(AiResult(kind=AiKind.CANNOT_ANSWER), c, SETTINGS)
    assert route is AiRoute.OFFER_STAFF and c.clarify_streak == 0

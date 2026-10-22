"""tests/scenarios/test_scenarios.py — ชุด regression บทสนทนา (docs/Line_ai_bot.md หัวข้อ 10 การสังเกตและทดสอบ)

 - อ่านทุกไฟล์ใน tests/scenarios/cases/*.toml (หนึ่งไฟล์ต่อบทสนทนา รูปแบบดู README.md)
 - หนึ่งไฟล์ = หนึ่งเทสต์: ส่งทุกรอบผ่าน run_turn ด้วยไฟล์กฎจริง (prompts/rules) + fake
 - AI เป็น fake ที่ตอบตามที่ไฟล์กำหนดทีละรอบ; รอบที่ไม่กำหนด ai/ai_error แล้ว AI ถูกเรียก -> fail
 - test_summary: นับเคสที่ควรส่งต่อแต่ไม่ส่งต่อ ("ไม่ส่งต่อขาด" ตัวชี้วัดสำคัญที่สุด) ต้องเป็น 0
   และพิมพ์ตารางสรุป (เห็นเมื่อรันด้วย -s)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.pipeline.orchestrator import TurnOutcome, TurnRoute, load_pipeline_config, run_turn
from app.pipeline.schemas import (
    AiBadResponse,
    AiRequest,
    AiResult,
    AiUnavailable,
    ConversationStatus,
    SendStatus,
)
from tests.fakes import FakeAiClient, make_deps, make_settings
from tests.pipeline.test_orchestrator import _raw

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = Path(__file__).resolve().parent / "cases"
RULES_DIR = ROOT / "prompts" / "rules"

PATHS = frozenset({"self_answer", "clarify", "handoff"})
SOURCES = frozenset({"synthetic", "real"})
CASE_KEYS = frozenset({"id", "source", "description", "expected_path", "turns"})
TURN_KEYS = frozenset(
    {"text", "postback", "ai", "ai_error", "advance_minutes", "route", "expect_status", "expect_sent"}
)
AI_KEYS = frozenset({"kind", "intro", "products", "clarify_question", "escalate_hint", "state_out"})
AI_ERRORS = {
    "unavailable": lambda: AiUnavailable("scenario", error_class="timeout"),
    "bad_response": lambda: AiBadResponse("scenario", error_class="bad_json"),
}
# เส้นทางที่แปลว่าส่งต่อพนักงานแล้ว
HANDOFF_ROUTES = frozenset(
    {
        TurnRoute.USER_ESCALATE,
        TurnRoute.RULE_ESCALATE,
        TurnRoute.AI_ESCALATE,
        TurnRoute.AI_DOWN_ESCALATE,
        TurnRoute.VALIDATE_ESCALATE,
    }
)


# --- อ่านไฟล์ ---


@dataclass(frozen=True)
class Turn:
    text: str | None
    postback: str | None
    ai: AiResult | None
    ai_error: str | None
    advance_minutes: float
    route: TurnRoute
    expect_status: ConversationStatus | None
    expect_sent: list[SendStatus] | None


@dataclass(frozen=True)
class Case:
    id: str
    source: str
    description: str
    expected_path: str
    turns: list[Turn]
    file: Path


def _unknown(where: str, data: dict[str, Any], allowed: frozenset[str]) -> None:
    extra = set(data) - allowed
    if extra:
        raise ValueError(f"{where}: ไม่รู้จักฟิลด์ {sorted(extra)}")


def _parse_turn(where: str, t: dict[str, Any]) -> Turn:
    _unknown(where, t, TURN_KEYS)
    if ("text" in t) == ("postback" in t):
        raise ValueError(f"{where}: ต้องมี text หรือ postback อย่างใดอย่างหนึ่ง")
    if "ai" in t and "ai_error" in t:
        raise ValueError(f"{where}: ใส่ ai กับ ai_error พร้อมกันไม่ได้")
    ai = None
    if "ai" in t:
        _unknown(f"{where}.ai", t["ai"], AI_KEYS)
        ai = AiResult.model_validate(t["ai"])
    ai_error = t.get("ai_error")
    if ai_error is not None and ai_error not in AI_ERRORS:
        raise ValueError(f"{where}: ai_error ต้องเป็น {sorted(AI_ERRORS)}")
    status = t.get("expect_status")
    sent = t.get("expect_sent")
    return Turn(
        text=t.get("text"),
        postback=t.get("postback"),
        ai=ai,
        ai_error=ai_error,
        advance_minutes=float(t.get("advance_minutes", 0)),
        route=TurnRoute(t["route"]),
        expect_status=ConversationStatus(status) if status is not None else None,
        expect_sent=[SendStatus(s) for s in sent] if sent is not None else None,
    )


def load_case(path: Path) -> Case:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    where = path.name
    _unknown(where, data, CASE_KEYS)
    if data.get("source") not in SOURCES:
        raise ValueError(f"{where}: source ต้องเป็น {sorted(SOURCES)}")
    if data.get("expected_path") not in PATHS:
        raise ValueError(f"{where}: expected_path ต้องเป็น {sorted(PATHS)}")
    turns = [_parse_turn(f"{where} turns[{i}]", t) for i, t in enumerate(data.get("turns", []))]
    if not turns:
        raise ValueError(f"{where}: ต้องมีอย่างน้อยหนึ่ง [[turns]]")
    case = Case(
        id=data["id"],
        source=data["source"],
        description=data.get("description", ""),
        expected_path=data["expected_path"],
        turns=turns,
        file=path,
    )
    # ป้ายต้องตรงกับ route ที่คาด: handoff <-> มีรอบที่ส่งต่อ
    expects_handoff = any(t.route in HANDOFF_ROUTES for t in turns)
    if expects_handoff != (case.expected_path == "handoff"):
        raise ValueError(f"{where}: expected_path={case.expected_path} ไม่ตรงกับ route ที่คาด")
    return case


CASE_FILES = sorted(CASES_DIR.glob("*.toml"))


# --- รัน ---


@dataclass
class CaseRun:
    case: Case
    outcomes: list[TurnOutcome] = field(default_factory=list)
    statuses: list[ConversationStatus | None] = field(default_factory=list)
    ai_calls_per_turn: list[int] = field(default_factory=list)

    @property
    def handed_off(self) -> bool:
        return any(o.route in HANDOFF_ROUTES for o in self.outcomes) or (
            ConversationStatus.HUMAN in self.statuses
        )


def _unscripted_ai(req: AiRequest) -> AiResult:
    raise AssertionError(f"AI ถูกเรียกในรอบที่ไม่ได้กำหนด ai/ai_error: {req.text_redacted!r}")


async def run_case(case: Case) -> CaseRun:
    settings = make_settings(rules_dir=RULES_DIR)
    cfg = load_pipeline_config(settings)
    ai = FakeAiClient(handler=_unscripted_ai)
    deps = make_deps(settings=settings, ai=ai)
    db = deps.raw_events.db  # type: ignore[attr-defined]
    run = CaseRun(case)
    for turn in case.turns:
        if turn.advance_minutes:
            deps.clock.advance(minutes=turn.advance_minutes)  # type: ignore[attr-defined]
        if turn.ai is not None:
            ai.responses.append(turn.ai)
        elif turn.ai_error is not None:
            ai.responses.append(AI_ERRORS[turn.ai_error]())
        calls_before = len(ai.calls)
        raw = await _raw(deps, text=turn.text, postback=turn.postback)
        run.outcomes.append(await run_turn(deps, raw, cfg))
        run.ai_calls_per_turn.append(len(ai.calls) - calls_before)
        ai.responses.clear()  # ผลที่กำหนดแต่ไม่ถูกใช้ ไม่ไหลไปรอบถัดไป
        open_convs = [c for c in db.conversations.values() if c.closed_at is None]
        run.statuses.append(open_convs[0].status if open_convs else None)
    return run


def _case_id(path: Path) -> str:
    return path.stem


@pytest.mark.parametrize("path", CASE_FILES, ids=_case_id)
async def test_scenario(path: Path) -> None:
    case = load_case(path)
    run = await run_case(case)
    for i, (turn, out, status, calls) in enumerate(
        zip(case.turns, run.outcomes, run.statuses, run.ai_calls_per_turn, strict=True)
    ):
        where = f"{case.id} รอบ {i + 1} ({turn.text or turn.postback!r})"
        assert out.route == turn.route, f"{where}: route {out.route} (reason={out.reason})"
        if turn.ai is not None or turn.ai_error is not None:
            assert calls == 1, f"{where}: กำหนดผล AI ไว้แต่ AI ถูกเรียก {calls} ครั้ง"
        if turn.expect_status is not None:
            assert status == turn.expect_status, f"{where}: status {status}"
        if turn.expect_sent is not None:
            assert out.sent == turn.expect_sent, f"{where}: sent {out.sent}"


async def test_summary() -> None:
    """ไม่ส่งต่อขาด = 0 (เคสที่ควรส่งต่อต้องส่งต่อทุกเคส) + ตารางสรุป"""
    assert CASE_FILES, "ยังไม่มีไฟล์ใน tests/scenarios/cases/"
    runs = [await run_case(load_case(p)) for p in CASE_FILES]

    def route_ok(r: CaseRun) -> bool:
        return all(o.route == t.route for o, t in zip(r.outcomes, r.case.turns, strict=True))

    should = [r for r in runs if r.case.expected_path == "handoff"]
    missed = [r.case.id for r in should if not r.handed_off]
    over = [r.case.id for r in runs if r.case.expected_path != "handoff" and r.handed_off]
    passed = sum(route_ok(r) for r in runs)
    real = sum(r.case.source == "real" for r in runs)

    print()
    print("scenarios (tests/scenarios/cases)")
    print(f"  ทั้งหมด                  {len(runs):>3}  (real {real}, synthetic {len(runs) - real})")
    print(f"  ผ่าน (route ตรงทุกรอบ)    {passed:>3}")
    print(f"  ควรส่งต่อ                {len(should):>3}")
    print(f"  ไม่ส่งต่อขาด              {len(missed):>3}  {missed or ''}")
    print(f"  ส่งต่อเกิน (over-escalated) {len(over):>3}  {over or ''}")
    if real < 30:
        print("  รอข้อมูลจริง: ต้องการ 30 บทสนทนาจากแชทจริง (ควรส่งต่อราว 10)")

    assert not missed, f"ไม่ส่งต่อขาด: {missed}"

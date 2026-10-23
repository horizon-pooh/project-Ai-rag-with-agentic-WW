"""tests/services/test_ai_client.py

เคสทดสอบของ app/services/ai_client.py และ ai_parsers.py (จำลอง HTTP ด้วย respx, ยังไม่มีฟังก์ชันทดสอบ)

รายละเอียด (สิ่งที่ API ทำ -> ผลที่คาด):
 - 200 + คำตอบถูกรูปแบบ -> AiResult (kind ถูก, products ครบ sku/name/price/url/image_url)
 - timeout ครั้งแรก แล้วครั้งที่สองสำเร็จ -> AiResult, attempts = 2
 - timeout ทั้งสองครั้ง -> AiUnavailable
 - ต่อไม่ได้ (connection error) -> ลองซ้ำ 1 ครั้ง
 - 429 มี retry-after และเวลาเหลือพอ -> รอตาม retry-after แล้วลองซ้ำ
 - 429 / 5xx แต่เวลาเหลือไม่พอ (ใกล้ deadline) -> ไม่ลองซ้ำ -> AiUnavailable
 - 5xx ครั้งเดียวแล้วสำเร็จ -> AiResult
 - 400 / 401 / 4xx อื่น -> ไม่ลองซ้ำ -> AiBadResponse
 - 200 แต่อ่านไม่ออก -> ไม่ลองซ้ำ -> AiBadResponse
 - รวมทุกรอบไม่เกิน deadline
 - Semaphore: งานพร้อมกันไม่เกิน AI_CONCURRENCY
 - request ที่ส่งออก = OpenAI chat {"model", "messages", "temperature"} ไป {AI_API_URL}/chat/completions
   ไม่มีข้อความดิบ ไม่มี token LINE ไม่มี state/flags (มีแค่แชทย้อนหลัง + text_redacted)
 - เลือก parser ตาม AI_RESPONSE_FORMAT (markdown ตั้งต้น / json); system prompt จากไฟล์ (ถ้าตั้ง)
 - MarkdownParser กับคำตอบจริง: tests/services/test_ai_parsers_markdown.py

แนวคิดจาก: src/ai/aiClient.js, src/ai/softnixParser.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว — ครบทุกเคสข้างบน (รูปแบบ request ตาม API จริงที่วัด 2026-10-23, D-011)
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.pipeline.schemas import (
    AI_RAW_MAX_CHARS,
    AiBadResponse,
    AiFlags,
    AiKind,
    AiRequest,
    AiResult,
    AiUnavailable,
    ChatTurn,
    Direction,
)
from app.services import ai_client as ai_client_mod
from app.services.ai_client import (
    MIN_RETRY_BUDGET_S,
    STUB_PREFIX,
    HttpAiClient,
    StubAiClient,
    build_messages,
    make_ai_client,
    make_parser,
    parse_retry_after,
)
from app.services.ai_parsers import JsonParser, MarkdownParser
from tests.fakes import make_settings
from tests.services._helpers import FakeTime, RecordingLogger

AI_BASE = "https://ai.example.test/v1"
AI_URL = AI_BASE + "/chat/completions"  # client ต่อ path เอง
AI_KEY = "ai-secret-key-xyz"
LINE_TOKEN = "line-access-token-should-never-leave"
RAW_TEXT = "สวัสดีค่ะ เบอร์ 0812345678 อยากได้ตู้เย็น"  # ข้อความดิบ (ยังไม่ redact) — ห้ามออกไปหา API
REDACTED_TEXT = "สวัสดีค่ะ เบอร์ [phone] อยากได้ตู้เย็น"

PRODUCTS_MD = (
    "แนะนำตู้เย็น 2 รุ่นค่ะ\n\n## Product List\n\n"
    "### 1. ตู้เย็น 2 ประตู\n"
    "![ตู้เย็น 2 ประตู](https://shop.example.test/i/RF-100.jpg)\n"
    "- Brand: X\n- Price: ฿12,990.00\n- Details: ทดสอบ\n- Product code: RF-100\n"
    "- Product link: [View product details](https://shop.example.test/p/RF-100)\n\n"
    "### 2. ตู้เย็น 1 ประตู\n"
    "- Brand: Y\n- Price: ฿5,990.00\n- Product code: RF-200\n"
    "- Product link: [View product details](https://shop.example.test/p/RF-200)\n"
)


def chat_body(content: str) -> dict:
    """ซอง chat.completion แบบที่ gateway ตอบจริง"""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gemma4-31b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "tool_calls": None},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# สัญญาเสนอเดิม (AI_RESPONSE_FORMAT=json)
PRODUCTS_BODY = {
    "kind": "products",
    "intro": "แนะนำตู้เย็น 2 รุ่นค่ะ",
    "products": [
        {
            "sku": "RF-100",
            "name": "ตู้เย็น 2 ประตู",
            "price": 12990,
            "url": "https://shop.example.test/p/RF-100",
            "image_url": "https://shop.example.test/i/RF-100.jpg",
        },
        {"sku": "RF-200", "name": "ตู้เย็น 1 ประตู", "price": None, "url": "https://shop.example.test/p/RF-200"},
    ],
    "state_out": {"category": "ตู้เย็น"},
}


def settings(**kw):
    values = {
        "ai_api_url": AI_BASE,
        "ai_api_key": AI_KEY,
        "ai_timeout_s": 30,
        "ai_concurrency": 4,
        "line_channel_access_token": LINE_TOKEN,
    }
    values.update(kw)
    return make_settings(**values)


def request() -> AiRequest:
    return AiRequest(
        text_redacted=REDACTED_TEXT,
        recent_messages=[ChatTurn(direction=Direction.OUT, text_redacted="ต้องการหาสินค้าอะไรคะ")],
        state={"category": None},
        flags=AiFlags(just_resumed=True),
    )


def ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body if body is not None else chat_body(PRODUCTS_MD))


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def logger(monkeypatch) -> RecordingLogger:
    rec = RecordingLogger()
    monkeypatch.setattr(ai_client_mod, "log", rec)
    return rec


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_client(http, fake_time: FakeTime, **kw) -> HttpAiClient:
    return HttpAiClient(settings(**kw), http=http, sleep=fake_time.sleep, monotonic=fake_time.monotonic)


def read_timeout(call) -> float:
    return call.request.extensions["timeout"]["read"]


def timeout_after(fake_time: FakeTime, seconds: float):
    """side effect: เวลาผ่านไป seconds แล้ว timeout"""

    def effect(req: httpx.Request):
        fake_time.advance(seconds)
        raise httpx.ReadTimeout("timed out", request=req)

    return effect


# --- สำเร็จ ---


@respx.mock
async def test_200_valid_returns_ai_result_with_products(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    result = await make_client(http, fake_time).call(request(), 50)

    assert route.call_count == 1
    assert result.kind is AiKind.PRODUCTS
    assert result.intro == "แนะนำตู้เย็น 2 รุ่นค่ะ"
    assert [p.sku for p in result.products] == ["RF-100", "RF-200"]
    p = result.products[0]
    assert (p.name, p.price, p.url, p.image_url) == (
        "ตู้เย็น 2 ประตู",
        Decimal("12990.00"),
        "https://shop.example.test/p/RF-100",
        "https://shop.example.test/i/RF-100.jpg",
    )
    assert result.products[1].price == Decimal("5990") and result.products[1].image_url is None
    assert result.state_out is None and result.escalate_hint is None  # API ไม่มีฟิลด์นี้
    assert result.attempts == 1
    assert result.latency_ms >= 0
    assert result.raw.startswith("แนะนำตู้เย็น")


@respx.mock
async def test_json_format_uses_json_parser(http, fake_time, logger):
    respx.post(AI_URL).mock(return_value=httpx.Response(200, json=PRODUCTS_BODY))
    result = await make_client(http, fake_time, ai_response_format="json").call(request(), 50)
    assert result.kind is AiKind.PRODUCTS
    assert result.state_out == {"category": "ตู้เย็น"}
    assert result.products[1].price is None
    assert json.loads(result.raw)["kind"] == "products"


@respx.mock
async def test_latency_ms_counts_elapsed_time(http, fake_time, logger):
    def effect(req):
        fake_time.advance(1.234)
        return ok()

    respx.post(AI_URL).mock(side_effect=effect)
    result = await make_client(http, fake_time).call(request(), 50)
    assert result.latency_ms == 1234


# --- timeout / ต่อไม่ได้ ---


@respx.mock
async def test_timeout_then_success_attempts_2(http, fake_time, logger):
    route = respx.post(AI_URL).mock(side_effect=[timeout_after(fake_time, 30), ok()])
    result = await make_client(http, fake_time).call(request(), 50)
    assert result.attempts == 2
    assert result.kind is AiKind.PRODUCTS
    assert route.call_count == 2
    assert fake_time.sleeps == []  # timeout ไม่ต้องรอก่อนลองซ้ำ


@respx.mock
async def test_timeout_twice_raises_unavailable(http, fake_time, logger):
    route = respx.post(AI_URL).mock(side_effect=timeout_after(fake_time, 20))
    with pytest.raises(AiUnavailable) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert ei.value.error_class == "timeout"
    assert ei.value.attempts == 2
    assert route.call_count == 2  # ลองซ้ำแค่ 1 ครั้ง


@respx.mock
async def test_connect_error_retries_once_then_success(http, fake_time, logger):
    route = respx.post(AI_URL).mock(side_effect=[httpx.ConnectError("refused"), ok()])
    result = await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 2
    assert result.attempts == 2


@respx.mock
async def test_connect_error_twice_raises_unavailable_connect(http, fake_time, logger):
    route = respx.post(AI_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AiUnavailable) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert (ei.value.error_class, ei.value.attempts, route.call_count) == ("connect", 2, 2)


# --- 429 / 5xx ---


@respx.mock
async def test_429_with_retry_after_waits_then_retries(http, fake_time, logger):
    route = respx.post(AI_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), ok()]
    )
    result = await make_client(http, fake_time).call(request(), 50)
    assert fake_time.sleeps == [3.0]
    assert route.call_count == 2
    assert result.attempts == 2


@respx.mock
async def test_429_twice_raises_unavailable_http_429(http, fake_time, logger):
    respx.post(AI_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}))
    with pytest.raises(AiUnavailable) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert (ei.value.error_class, ei.value.attempts) == ("http_429", 2)


@respx.mock
async def test_429_retry_after_not_less_than_remaining_no_retry(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "60"}))
    with pytest.raises(AiUnavailable) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 1
    assert ei.value.error_class == "http_429"
    assert ei.value.attempts == 1
    assert fake_time.sleeps == []  # ไม่รอให้เสียเวลา


@respx.mock
async def test_429_retry_after_leaves_too_little_time_no_retry(http, fake_time, logger):
    # เหลือ 50 วิ รอ 49.5 แล้วเหลือไม่ถึง MIN_RETRY_BUDGET_S
    wait = 50 - MIN_RETRY_BUDGET_S / 2
    route = respx.post(AI_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": str(wait)})
    )
    with pytest.raises(AiUnavailable):
        await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 1


@respx.mock
async def test_5xx_near_deadline_no_retry(http, fake_time, logger):
    def effect(req):
        fake_time.advance(49.8)  # ช้าจนเกือบหมดเวลา
        return httpx.Response(503)

    route = respx.post(AI_URL).mock(side_effect=effect)
    with pytest.raises(AiUnavailable) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 1
    assert (ei.value.error_class, ei.value.attempts) == ("http_5xx", 1)


@respx.mock
async def test_timeout_near_deadline_no_retry(http, fake_time, logger):
    route = respx.post(AI_URL).mock(side_effect=timeout_after(fake_time, 30))
    with pytest.raises(AiUnavailable) as ei:
        # timeout รอบแรกกิน 30 วิ เหลือ 0.5 วิ < MIN_RETRY_BUDGET_S
        await make_client(http, fake_time).call(request(), 30.5)
    assert route.call_count == 1
    assert ei.value.attempts == 1


@respx.mock
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_once_then_success(http, fake_time, logger, status):
    route = respx.post(AI_URL).mock(side_effect=[httpx.Response(status), ok()])
    result = await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 2
    assert result.attempts == 2


@respx.mock
async def test_5xx_respects_retry_after(http, fake_time, logger):
    respx.post(AI_URL).mock(side_effect=[httpx.Response(503, headers={"Retry-After": "2"}), ok()])
    await make_client(http, fake_time).call(request(), 50)
    assert fake_time.sleeps == [2.0]


# --- 4xx อื่น / อ่านไม่ออก ---


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_other_4xx_no_retry_bad_response(http, fake_time, logger, status):
    route = respx.post(AI_URL).mock(return_value=httpx.Response(status, json={"error": "x"}))
    with pytest.raises(AiBadResponse) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 1
    assert ei.value.error_class == f"http_{status}"
    assert ei.value.attempts == 1


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"<html>oops</html>",
        json.dumps(["choices"]).encode(),  # ไม่ใช่ object
        json.dumps({"kind": "answer", "intro": "ไม่ใช่ซอง chat"}).encode(),  # ไม่มี choices
        json.dumps({"choices": []}).encode(),
        json.dumps({"choices": [{"message": {"content": "   "}}]}).encode(),  # content ว่าง
        json.dumps({"choices": [{"message": {"content": None}}]}).encode(),
        json.dumps({"choices": [{"finish_reason": "stop"}]}).encode(),  # ไม่มี message
        # มีแต่รายการที่ถูกตัด ไม่มีข้อความเกริ่น -> ไม่มีอะไรใช้ได้
        json.dumps(chat_body("## Product List\n\n### 1. ถูกตัด\n- Price: ฿1")).encode(),
        b"\xff\xfe\x00bad-utf8",
    ],
)
async def test_200_unreadable_no_retry_bad_response(http, fake_time, logger, body):
    route = respx.post(AI_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(AiBadResponse) as ei:
        await make_client(http, fake_time).call(request(), 50)
    assert route.call_count == 1
    assert ei.value.error_class == "parse"
    assert ei.value.attempts == 1


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        json.dumps({"kind": "dance"}).encode(),  # kind ไม่รู้จัก
        json.dumps({"intro": "ไม่มี kind"}).encode(),  # ขาดฟิลด์บังคับ
        json.dumps(["products"]).encode(),  # ไม่ใช่ object
        json.dumps({"kind": "products", "products": [{"name": "ไม่มี sku", "url": "u"}]}).encode(),
        b"\xff\xfe\x00bad-utf8",
    ],
)
async def test_200_unreadable_json_format_bad_response(http, fake_time, logger, body):
    route = respx.post(AI_URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(AiBadResponse) as ei:
        await make_client(http, fake_time, ai_response_format="json").call(request(), 50)
    assert route.call_count == 1
    assert ei.value.error_class == "parse"


# --- deadline ---


@respx.mock
async def test_total_never_exceeds_deadline_and_per_attempt_timeout_is_capped(http, fake_time, logger):
    """ครั้งแรกใช้ AI_TIMEOUT_S เต็ม (30) ครั้งที่สองได้แค่เวลาที่เหลือ (20) รวม = deadline พอดี"""
    start = fake_time.now
    route = respx.post(AI_URL).mock(
        side_effect=[timeout_after(fake_time, 30), timeout_after(fake_time, 20)]
    )
    with pytest.raises(AiUnavailable):
        await make_client(http, fake_time).call(request(), 50)
    assert read_timeout(route.calls[0]) == 30
    assert read_timeout(route.calls[1]) == pytest.approx(20)
    assert fake_time.now - start <= 50


@respx.mock
async def test_per_attempt_timeout_capped_by_short_deadline(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    await make_client(http, fake_time).call(request(), 7)
    assert read_timeout(route.calls[0]) == 7


@respx.mock
async def test_retry_after_wait_counts_against_deadline(http, fake_time, logger):
    start = fake_time.now
    route = respx.post(AI_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "10"}), ok()]
    )
    await make_client(http, fake_time).call(request(), 50)
    # รอ 10 แล้วเหลือ 40 -> ครั้งที่สองได้ min(30, 40) = 30
    assert read_timeout(route.calls[1]) == 30
    assert fake_time.now - start <= 50


@respx.mock
async def test_deadline_already_passed_no_request(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    with pytest.raises(AiUnavailable) as ei:
        await make_client(http, fake_time).call(request(), 0)
    assert route.call_count == 0
    assert ei.value.error_class == "timeout"


@respx.mock
async def test_real_timeout_capped_by_asyncio_timeout(http, logger):
    """API ค้างนานกว่า deadline จริง -> ตัดที่ deadline (เวลาจริง ไม่ใช้ FakeTime)"""

    async def hang(req):
        await asyncio.sleep(5)
        return ok()

    respx.post(AI_URL).mock(side_effect=hang)
    client = HttpAiClient(settings(), http=http)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    with pytest.raises(AiUnavailable) as ei:
        await client.call(request(), 0.2)
    assert loop.time() - t0 < 2
    assert ei.value.error_class == "timeout"


# --- Semaphore ---


@respx.mock
async def test_semaphore_limits_concurrency(http, logger):
    in_flight = 0
    max_in_flight = 0

    async def slow(req):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return ok()

    respx.post(AI_URL).mock(side_effect=slow)
    client = HttpAiClient(settings(ai_concurrency=2), http=http)
    results = await asyncio.gather(*(client.call(request(), 5) for _ in range(6)))
    assert len(results) == 6
    assert max_in_flight == 2


@respx.mock
async def test_semaphore_wait_counts_against_deadline(http, logger):
    release = asyncio.Event()
    started = 0

    async def blocked(req):
        nonlocal started
        started += 1
        await release.wait()
        return ok()

    respx.post(AI_URL).mock(side_effect=blocked)
    client = HttpAiClient(settings(ai_concurrency=1), http=http)
    first = asyncio.create_task(client.call(request(), 5))
    await asyncio.sleep(0.01)  # ให้ตัวแรกถือ Semaphore
    with pytest.raises(AiUnavailable) as ei:
        await client.call(request(), 0.05)
    assert ei.value.error_class == "timeout"
    assert ei.value.attempts == 0
    assert started == 1  # ตัวที่สองไม่ได้ยิง HTTP
    release.set()
    assert (await first).kind is AiKind.PRODUCTS
    assert started == 1


async def test_semaphore_is_per_instance():
    a = HttpAiClient(settings(ai_concurrency=1))
    b = HttpAiClient(settings(ai_concurrency=1))
    assert a._sem is not b._sem
    await a.aclose()
    await b.aclose()


# --- สิ่งที่ส่งออก / ความลับ ---


@respx.mock
async def test_request_contains_only_redacted_text_and_no_line_token(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    await make_client(http, fake_time).call(request(), 50)

    sent = route.calls[0].request
    everything = sent.content.decode() + repr(dict(sent.headers))
    assert LINE_TOKEN not in everything
    assert RAW_TEXT not in everything
    assert "0812345678" not in everything
    # state / flags / รูป ไม่ส่ง (gateway ไม่มีช่องรับ และไม่รู้ว่าจะกวน prompt อย่างไร)
    assert set(json.loads(sent.content)) == {"model", "messages", "temperature"}
    for name in ("category", "state", "just_resumed", "flags", "images"):
        assert name not in sent.content.decode()


@respx.mock
async def test_request_is_openai_chat_with_history_and_redacted_text(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    await make_client(http, fake_time).call(request(), 50)

    sent = route.calls[0].request
    assert str(sent.url) == AI_URL
    assert json.loads(sent.content) == {
        "model": "gemma4-31b",
        "messages": [
            {"role": "assistant", "content": "ต้องการหาสินค้าอะไรคะ"},
            {"role": "user", "content": REDACTED_TEXT},
        ],
        "temperature": 0.2,
    }
    assert sent.headers["authorization"] == f"Bearer {AI_KEY}"


def test_build_messages_maps_directions_in_order():
    req = AiRequest(
        text_redacted="เอาสีดำ",
        recent_messages=[
            ChatTurn(direction=Direction.IN, text_redacted="มีตู้เย็นไหม"),
            ChatTurn(direction=Direction.OUT, text_redacted="มีค่ะ\nสินค้าแนะนำ: A, B"),
            ChatTurn(direction=Direction.IN, text_redacted="งบ 20000"),
        ],
    )
    assert build_messages(req) == [
        {"role": "user", "content": "มีตู้เย็นไหม"},
        {"role": "assistant", "content": "มีค่ะ\nสินค้าแนะนำ: A, B"},
        {"role": "user", "content": "งบ 20000"},
        {"role": "user", "content": "เอาสีดำ"},
    ]
    assert build_messages(AiRequest(text_redacted="x"), "กติกา") == [
        {"role": "system", "content": "กติกา"},
        {"role": "user", "content": "x"},
    ]


@respx.mock
async def test_system_prompt_file_prepended(http, fake_time, logger, tmp_path):
    prompt = tmp_path / "system.md"
    prompt.write_text("  ตอบสั้นๆ  \n", encoding="utf-8")
    route = respx.post(AI_URL).mock(return_value=ok())
    await make_client(http, fake_time, ai_system_prompt_file=prompt).call(request(), 50)
    messages = json.loads(route.calls[0].request.content)["messages"]
    assert messages[0] == {"role": "system", "content": "ตอบสั้นๆ"}
    assert messages[-1] == {"role": "user", "content": REDACTED_TEXT}


def test_missing_system_prompt_file_fails_at_start(tmp_path):
    with pytest.raises(FileNotFoundError):
        HttpAiClient(settings(ai_system_prompt_file=tmp_path / "nope.md"))


@respx.mock
async def test_model_and_temperature_from_settings(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    await make_client(http, fake_time, ai_model="other-model", ai_temperature=0).call(request(), 50)
    body = json.loads(route.calls[0].request.content)
    assert (body["model"], body["temperature"]) == ("other-model", 0)


def test_make_parser_follows_setting():
    assert isinstance(make_parser(settings()), MarkdownParser)
    assert isinstance(make_parser(settings(ai_response_format="json")), JsonParser)
    with pytest.raises(ValueError):
        settings(ai_response_format="xml")


@respx.mock
async def test_no_authorization_header_without_key(http, fake_time, logger):
    route = respx.post(AI_URL).mock(return_value=ok())
    await make_client(http, fake_time, ai_api_key=None).call(request(), 50)
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
async def test_logs_never_contain_text_or_key(http, fake_time, logger):
    respx.post(AI_URL).mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(503), httpx.Response(400)]
    )
    client = make_client(http, fake_time)
    with pytest.raises(AiUnavailable):
        await client.call(request(), 50)
    with pytest.raises(AiBadResponse):
        await client.call(request(), 50)
    assert logger.records  # log จริง
    dumped = logger.dump()
    for secret in (AI_KEY, LINE_TOKEN, REDACTED_TEXT, "ต้องการหาสินค้าอะไรคะ"):
        assert secret not in dumped


def test_http_client_requires_url():
    with pytest.raises(ValueError):
        HttpAiClient(make_settings())


@pytest.mark.parametrize(
    ("value", "expected"),
    [("3", 3.0), (" 1.5 ", 1.5), ("0", 0.0), (None, None), ("", None), ("-1", None),
     ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("inf", None), ("nan", None)],
)
def test_parse_retry_after(value, expected):
    assert parse_retry_after(value) == expected


# --- JsonParser ---


def test_json_parser_overrides_client_fields_and_ignores_extra():
    body = json.dumps(
        {"kind": "answer", "intro": "ได้ค่ะ", "raw": "fake", "latency_ms": 99999, "attempts": 7, "extra": 1}
    )
    result = JsonParser().parse(body, latency_ms=12, attempts=2)
    assert (result.kind, result.intro, result.latency_ms, result.attempts) == (AiKind.ANSWER, "ได้ค่ะ", 12, 2)
    assert result.raw == body


@pytest.mark.parametrize("kind", list(AiKind))
def test_json_parser_accepts_every_kind(kind):
    body = {"kind": kind.value}
    if kind is AiKind.CLARIFY:
        body["clarify_question"] = "ต้องการขนาดเท่าไหร่คะ"
    result = JsonParser().parse(json.dumps(body).encode(), latency_ms=1, attempts=1)
    assert result.kind is kind
    assert result.escalate_hint is None and result.state_out is None  # API ไม่บอก = None (รอคำตอบ)


def test_json_parser_truncates_raw():
    body = json.dumps({"kind": "answer", "intro": "ก" * (AI_RAW_MAX_CHARS * 2)})
    result = JsonParser().parse(body, latency_ms=1, attempts=1)
    assert len(result.raw) <= AI_RAW_MAX_CHARS + len("…[truncated]")
    assert result.raw.endswith("…[truncated]")


def test_json_parser_error_does_not_echo_values():
    body = json.dumps({"kind": "ข้อความลับ 0812345678"})
    with pytest.raises(AiBadResponse) as ei:
        JsonParser().parse(body, latency_ms=3, attempts=1)
    assert "0812345678" not in str(ei.value)
    assert ei.value.latency_ms == 3


# --- Stub / factory ---


async def test_stub_client_echoes_redacted_text():
    result = await StubAiClient().call(request(), 50)
    assert isinstance(result, AiResult)
    assert result.kind is AiKind.ANSWER
    assert result.intro == STUB_PREFIX + REDACTED_TEXT


async def test_stub_client_caps_echo_length():
    result = await StubAiClient().call(AiRequest(text_redacted="ข" * 6000), 50)
    assert result.intro is not None and len(result.intro) < 1000


async def test_make_ai_client_picks_stub_or_http(monkeypatch):
    monkeypatch.setattr(ai_client_mod, "log", RecordingLogger())
    assert isinstance(make_ai_client(make_settings()), StubAiClient)
    client = make_ai_client(settings())
    assert isinstance(client, HttpAiClient)
    await client.aclose()

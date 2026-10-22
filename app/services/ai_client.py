"""app/services/ai_client.py

client ของ AI API ภายนอก: ที่เดียวที่รู้จักรูปแบบ API

รายละเอียด:
 - รับ AiRequest คืน AiResult (ใช้ ai_parsers แปลงผล)
 - httpx async + timeout (AI_TIMEOUT_S) ทุกครั้ง
 - asyncio.Semaphore(AI_CONCURRENCY) จำกัดงานพร้อมกันตามที่ API รับไหว
 - ลองซ้ำ: timeout/ต่อไม่ได้/429/5xx -> 1 ครั้งถ้าเวลาเหลือพอ (เคารพ retry-after); 4xx อื่น/อ่านไม่ออก -> ไม่ซ้ำ
 - รวมทุกรอบไม่เกิน deadline ที่ส่งเข้ามา
 - error: AiUnavailable (ลองแล้วยังพัง), AiBadResponse (อ่านไม่ออก/4xx)
 - ส่งเฉพาะข้อความ redact แล้ว
 - รอคำตอบ: API คือ Softnix หรือไม่, JSON/markdown, คืน state หรือไม่, รับรูปได้หรือไม่

แนวคิดจาก: src/ai/aiClient.js, src/ai/llmClient.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว — HttpAiClient ใช้สัญญาเสนอของเรา (รอคำตอบ: รูปแบบจริงของ API) + StubAiClient เมื่อไม่มี AI_API_URL
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

import httpx
import structlog

from app.config import Settings
from app.pipeline.ports import AiClient
from app.pipeline.schemas import AiBadResponse, AiKind, AiRequest, AiResult, AiUnavailable
from app.services.ai_parsers import AiParser, JsonParser

log = structlog.get_logger(__name__)

# ลองได้ทั้งหมดกี่ครั้ง (ครั้งแรก + ซ้ำ 1)
MAX_ATTEMPTS = 2
# (เสนอ) เวลาขั้นต่ำที่ต้องเหลือหลังรอ retry-after ถึงจะคุ้มลองซ้ำ — รอคำตอบ: ความเร็วจริงของ API
MIN_RETRY_BUDGET_S = 1.0
# ข้อความที่ StubAiClient ทวนกลับ (กันเกินเพดาน text ของ LINE)
STUB_ECHO_MAX_CHARS = 500
STUB_PREFIX = "(โหมดทดสอบ ยังไม่ได้ต่อ AI API) ได้รับข้อความ: "

Sleep = Callable[[float], Awaitable[object]]
Monotonic = Callable[[], float]


class _Retryable(Exception):
    """ความพังที่ลองซ้ำได้ของหนึ่งรอบ (ใช้ภายในเท่านั้น)"""

    def __init__(self, error_class: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(error_class)
        self.error_class = error_class
        self.status_code = status_code
        self.retry_after = retry_after


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After แบบวินาที (เช่น "3" หรือ "1.5") -> float; ไม่มี/รูปแบบอื่น (เช่น HTTP-date) -> None"""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


class HttpAiClient:
    """เรียก AI API ด้วย HTTP — สัญญาเสนอของเรา (รอคำตอบ: รูปแบบจริงของ API)

    - POST AI_API_URL, body = AiRequest.model_dump(mode="json"), header Authorization: Bearer AI_API_KEY
    - ตอบ 2xx -> parser (ตั้งต้น JsonParser) แปลงเป็น AiResult
    - Semaphore เป็นของ instance (ไม่ใช่ global): หนึ่ง process ควรสร้าง client ตัวเดียวแล้วใช้ร่วมกัน
    - เวลาที่รอ Semaphore นับรวมใน deadline และใน latency_ms
    - ห้าม log ข้อความของ request และ key — log แค่ attempt / error_class / status_code
    """

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        parser: AiParser | None = None,
        sleep: Sleep = asyncio.sleep,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        if not settings.ai_api_url:
            raise ValueError("HttpAiClient ต้องมี AI_API_URL (ไม่มีให้ใช้ make_ai_client -> StubAiClient)")
        self._url = settings.ai_api_url
        self._key = settings.ai_api_key
        self._timeout_s = settings.ai_timeout_s
        self._sem = asyncio.Semaphore(settings.ai_concurrency)
        self._owns_http = http is None
        self._http = http if http is not None else httpx.AsyncClient()
        self._parser: AiParser = parser if parser is not None else JsonParser()
        self._sleep = sleep
        self._monotonic = monotonic

    async def aclose(self) -> None:
        """ปิด httpx client ถ้าสร้างเอง (ถ้ารับมาจากข้างนอก ผู้สร้างเป็นคนปิด)"""
        if self._owns_http:
            await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._key is not None and self._key.get_secret_value():
            headers["Authorization"] = f"Bearer {self._key.get_secret_value()}"
        return headers

    async def call(self, req: AiRequest, deadline_s: float) -> AiResult:
        start = self._monotonic()
        end = start + deadline_s

        def remaining() -> float:
            return end - self._monotonic()

        def elapsed_ms() -> int:
            return max(0, int((self._monotonic() - start) * 1000))

        def unavailable(error_class: str, attempts: int) -> AiUnavailable:
            log.warning("ai_unavailable", error_class=error_class, attempts=attempts, latency_ms=elapsed_ms())
            return AiUnavailable(
                f"AI API ใช้ไม่ได้: {error_class}",
                error_class=error_class,
                attempts=attempts,
                latency_ms=elapsed_ms(),
            )

        if remaining() <= 0:
            raise unavailable("timeout", 0)
        # รอคิวของ Semaphore ก็กินเวลา deadline
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=remaining())
        except TimeoutError:
            raise unavailable("timeout", 0) from None
        try:
            return await self._call_with_retry(req, remaining, elapsed_ms, unavailable)
        finally:
            self._sem.release()

    async def _call_with_retry(
        self,
        req: AiRequest,
        remaining: Callable[[], float],
        elapsed_ms: Callable[[], int],
        unavailable: Callable[[str, int], AiUnavailable],
    ) -> AiResult:
        payload = req.model_dump(mode="json")
        headers = self._headers()
        attempt = 0
        while True:
            attempt += 1
            budget = remaining()
            if budget <= 0:
                raise unavailable("timeout", attempt - 1)
            per_attempt_s = min(self._timeout_s, budget)
            try:
                resp = await self._post(payload, headers, per_attempt_s)
            except _Retryable as failure:
                log.warning(
                    "ai_attempt_failed",
                    attempt=attempt,
                    error_class=failure.error_class,
                    status_code=failure.status_code,
                )
                if attempt >= MAX_ATTEMPTS:
                    raise unavailable(failure.error_class, attempt) from None
                wait_s = failure.retry_after or 0.0
                left = remaining()
                # รอ retry-after แล้วต้องเหลือเวลาพอลองอีกรอบ ไม่งั้นไป fallback เลย
                if wait_s >= left or left - wait_s < MIN_RETRY_BUDGET_S:
                    raise unavailable(failure.error_class, attempt) from None
                if wait_s > 0:
                    await self._sleep(wait_s)
                continue

            status = resp.status_code
            if 200 <= status < 300:
                return self._parser.parse(resp.content, latency_ms=elapsed_ms(), attempts=attempt)
            log.warning("ai_bad_status", attempt=attempt, status_code=status)
            raise AiBadResponse(
                f"AI API ตอบ HTTP {status}",
                error_class=f"http_{status}",
                attempts=attempt,
                latency_ms=elapsed_ms(),
            )

    async def _post(self, payload: dict, headers: dict[str, str], timeout_s: float) -> httpx.Response:
        """หนึ่งรอบ: คืน response ที่ไม่ใช่ 429/5xx; ที่ลองซ้ำได้ -> _Retryable"""
        try:
            # httpx timeout นับแยกแต่ละช่วง (connect/read/...) จึงครอบด้วย asyncio.timeout ให้รวมไม่เกินด้วย
            async with asyncio.timeout(timeout_s):
                resp = await self._http.post(
                    self._url, json=payload, headers=headers, timeout=httpx.Timeout(timeout_s)
                )
        except (httpx.TimeoutException, TimeoutError):
            raise _Retryable("timeout") from None
        except httpx.TransportError:
            raise _Retryable("connect") from None
        status = resp.status_code
        if status == 429:
            raise _Retryable(
                "http_429", status_code=status, retry_after=parse_retry_after(resp.headers.get("retry-after"))
            )
        if status >= 500:
            raise _Retryable(
                "http_5xx", status_code=status, retry_after=parse_retry_after(resp.headers.get("retry-after"))
            )
        return resp


class StubAiClient:
    """ใช้เมื่อไม่ได้ตั้ง AI_API_URL: ตอบ kind=answer ทวนข้อความ (redact แล้ว) ให้สายบางๆ ทำงานครบจนส่ง LINE ได้"""

    async def call(self, req: AiRequest, deadline_s: float) -> AiResult:
        text = req.text_redacted
        if len(text) > STUB_ECHO_MAX_CHARS:
            text = text[:STUB_ECHO_MAX_CHARS] + "…"
        return AiResult(kind=AiKind.ANSWER, intro=STUB_PREFIX + text, latency_ms=0, attempts=1)


def make_ai_client(settings: Settings) -> AiClient:
    """AI_API_URL ตั้งแล้ว -> HttpAiClient; ว่าง -> StubAiClient (log เตือน)"""
    if settings.ai_enabled:
        return HttpAiClient(settings)
    log.warning("ai_stub_mode", reason="ไม่ได้ตั้ง AI_API_URL — ใช้ StubAiClient")
    return StubAiClient()


__all__ = [
    "MAX_ATTEMPTS",
    "MIN_RETRY_BUDGET_S",
    "STUB_PREFIX",
    "HttpAiClient",
    "StubAiClient",
    "make_ai_client",
    "parse_retry_after",
]

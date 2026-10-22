"""app/services/ai_parsers.py

แปลงคำตอบดิบของ AI API เป็น AiResult

รายละเอียด:
 - JsonParser: ถ้า API ตอบเป็น JSON
 - MarkdownParser: ถ้าตอบเป็น markdown (แบบ softnixParser เดิม)
 - เลือกตัวไหน: รอคำตอบเรื่อง AI API ข้อ 2
 - markdown ต้องมีเคสทดสอบจากคำตอบจริง 20-30 ตัวอย่าง
 - อ่านไม่ออก -> AiBadResponse (ไม่เดา)
 - เก็บ raw ตัดความยาวไว้ใน AiResult เพื่อ debug

แนวคิดจาก: src/ai/softnixParser.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว — JsonParser ตามสัญญาเสนอ (รอคำตอบ: รูปแบบจริงของ API); MarkdownParser ยังเป็น stub
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import ValidationError

from app.pipeline.schemas import AI_RAW_MAX_CHARS, AiBadResponse, AiResult

# ฟิลด์ที่ client เป็นคนตั้ง ไม่เชื่อค่าจาก API
_CLIENT_FIELDS = ("raw", "latency_ms", "attempts")

PARSE_ERROR_CLASS = "parse"


class AiParser(Protocol):
    """ตัวแปลงคำตอบดิบ -> AiResult; อ่านไม่ออก -> AiBadResponse(error_class="parse")"""

    def parse(self, body: bytes | str, *, latency_ms: int, attempts: int) -> AiResult: ...


def _to_text(body: bytes | str) -> str:
    if isinstance(body, str):
        return body
    return body.decode("utf-8")


def _truncate(text: str) -> str:
    # AiResult ตัดซ้ำอีกชั้นอยู่แล้ว ตรงนี้กันข้อความยาวมากไม่ให้ค้างในหน่วยความจำ/ข้อความ error
    return text[: AI_RAW_MAX_CHARS + 1]


class JsonParser:
    """สัญญาเสนอ (รอคำตอบ): body เป็น JSON object ที่ชื่อฟิลด์ตรงกับ AiResult
    เช่น {"kind": "products", "intro": "...", "products": [{"sku", "name", "price", "url", "image_url"}],
    "clarify_question": null, "escalate_hint": null, "state_out": {...}}

    - kind ไม่รู้จัก / ขาดฟิลด์บังคับ / JSON เสีย / ไม่ใช่ object -> AiBadResponse(error_class="parse")
    - ฟิลด์เกินไม่ทำให้พัง (AiResult ignore)
    - raw / latency_ms / attempts จาก API ไม่ใช้ (client ตั้งเอง)
    """

    def parse(self, body: bytes | str, *, latency_ms: int, attempts: int) -> AiResult:
        try:
            text = _to_text(body)
        except UnicodeDecodeError as e:
            raise self._bad("คำตอบไม่ใช่ UTF-8", latency_ms, attempts) from e
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise self._bad("คำตอบไม่ใช่ JSON", latency_ms, attempts) from e
        if not isinstance(data, dict):
            raise self._bad("คำตอบ JSON ไม่ใช่ object", latency_ms, attempts)
        fields = {k: v for k, v in data.items() if k not in _CLIENT_FIELDS}
        try:
            return AiResult.model_validate(
                {**fields, "raw": _truncate(text), "latency_ms": latency_ms, "attempts": attempts}
            )
        except ValidationError as e:
            # ไม่ใส่ค่าที่ไม่ผ่านลงข้อความ error (อาจมีข้อความของลูกค้าที่ API ทวนกลับมา) — บอกแค่ตำแหน่ง
            where = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
            raise self._bad(f"คำตอบผิดรูปแบบที่: {', '.join(where)}", latency_ms, attempts) from None

    @staticmethod
    def _bad(message: str, latency_ms: int, attempts: int) -> AiBadResponse:
        return AiBadResponse(
            message, error_class=PARSE_ERROR_CLASS, attempts=attempts, latency_ms=latency_ms
        )


class MarkdownParser:
    """รอคำตอบ: ใช้เมื่อ API ตอบเป็น markdown (แบบ softnixParser เดิม) — ยังไม่เขียนเพราะต้องมีตัวอย่างจริงก่อน"""

    def parse(self, body: bytes | str, *, latency_ms: int, attempts: int) -> AiResult:
        raise NotImplementedError(
            "รอคำตอบ: API ตอบเป็น markdown หรือไม่ ต้องมีตัวอย่างจริง 20–30 ชิ้น"
        )


__all__ = ["PARSE_ERROR_CLASS", "AiParser", "JsonParser", "MarkdownParser"]

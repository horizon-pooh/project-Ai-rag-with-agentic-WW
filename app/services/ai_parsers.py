"""app/services/ai_parsers.py

แปลงคำตอบดิบของ AI API เป็น AiResult

รายละเอียด:
 - MarkdownParser (ตั้งต้น, AI_RESPONSE_FORMAT=markdown): body = chat.completion ของ gateway แบบ OpenAI
   เนื้อหาใน choices[0].message.content เป็น markdown (วัดจากคำตอบจริง 2026-10-23, docs/decisions.md D-011)
 - JsonParser (AI_RESPONSE_FORMAT=json): สัญญาเสนอเดิมของเรา body เป็น JSON ตามฟิลด์ AiResult
 - markdown ต้องมีเคสทดสอบจากคำตอบจริง 20-30 ตัวอย่าง (ตอนนี้มี 7 ใน tests/services/fixtures/ai_responses/)
 - อ่านไม่ออก -> AiBadResponse (ไม่เดา)
 - เก็บ raw ตัดความยาวไว้ใน AiResult เพื่อ debug

แนวคิดจาก: src/ai/softnixParser.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว — MarkdownParser ตามรูปแบบที่วัดจาก API จริง 7 ตัวอย่าง (kind เป็น heuristic รอคำตอบ);
       JsonParser ตามสัญญาเสนอเดิม (เผื่อ API เปลี่ยน)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit

import structlog
from pydantic import ValidationError

from app.pipeline.schemas import AI_RAW_MAX_CHARS, AiBadResponse, AiKind, AiResult, Product

log = structlog.get_logger(__name__)

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


def _bad(message: str, latency_ms: int, attempts: int) -> AiBadResponse:
    return AiBadResponse(
        message, error_class=PARSE_ERROR_CLASS, attempts=attempts, latency_ms=latency_ms
    )


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
        return _bad(message, latency_ms, attempts)


# --- MarkdownParser ---

# หัวรายการสินค้า "## Product List" (ระดับหัวข้อไม่สนใจ)
_LIST_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}[ \t]*Product List[ \t]*#*[ \t]*$", re.IGNORECASE | re.MULTILINE
)
# หัวสินค้า "### 1. ชื่อ"
_PRODUCT_HEADING_RE = re.compile(r"^[ \t]{0,3}#{3,6}[ \t]+(?:\d+[ \t]*[.)][ \t]*)?(?P<name>.*?)[ \t]*$")
# ช่องข้อมูล "- Price: ฿14,999.00" (ชื่อช่องเป็นอังกฤษ อาจมี ** ครอบ)
_FIELD_RE = re.compile(
    r"^[ \t]*[-*+][ \t]+(?:\*\*)?(?P<key>[A-Za-z][A-Za-z ]*?)[ \t]*(?:\*\*)?[ \t]*:"
    r"[ \t]*(?:\*\*)?[ \t]*(?P<value>.*?)[ \t]*$"
)
_IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\((?P<url>[^)\s]+)\)")
_LINK_RE = re.compile(r"\[(?P<text>[^\]\n]*)\]\((?P<url>[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"^(?P<url>https://\S+)$")
_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]")
_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

_KEY_SKU = frozenset({"product code", "code", "sku"})
_KEY_PRICE = frozenset({"price"})
_KEY_LINK = frozenset({"product link", "link", "url"})

# เครื่องหมายคำถาม (ครึ่งความกว้าง / เต็มความกว้าง)
_QUESTION_MARKS = ("?", "？")


@dataclass
class _Block:
    """หนึ่งก้อน "### N. ชื่อ" ระหว่างอ่าน"""

    name: str
    fields: dict[str, str] = field(default_factory=dict)
    image_url: str | None = None


def _https_url(value: str | None) -> str | None:
    """คืน value ถ้าเป็น https ที่มี host และไม่มีช่องว่าง ไม่งั้น None"""
    if not value or any(c.isspace() for c in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return value


def _parse_price(value: str) -> Decimal | None:
    """ "฿14,999.00" -> Decimal("14999.00"); ไม่มีตัวเลข -> None"""
    m = _NUMBER_RE.search(value)
    if m is None:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:  # pragma: no cover - regex รับแต่ตัวเลข
        return None


def _strip_inline(text: str) -> str:
    """ลบ **ตัวหนา** / *เอียง* / `code` ในข้อความบรรทัดเดียว (ชื่อสินค้า, ค่าในช่อง, บรรทัดของคำตอบ)"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    return text.replace("**", "").rstrip()


def to_line_text(markdown: str, *, keep_link_urls: bool) -> str:
    """markdown -> ข้อความธรรมดาสำหรับ LINE (LINE text ไม่แสดง markdown)

    - ลบรูป ![..](..), เครื่องหมายหัวข้อ #, เส้นคั่น, **ตัวหนา**, *เอียง*, `code`
    - ลิงก์ [ข้อความ](url): keep_link_urls=True -> "ข้อความ (url)" ไม่งั้นเหลือแค่ข้อความ
      (validate ลบ URL ที่ไม่ใช่ของสินค้าออกอีกชั้นอยู่แล้ว)
    - หัวข้อย่อย "* " -> "- "; ยุบบรรทัดว่าง 3+ เหลือ 2
    """
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _IMAGE_RE.sub("", text)

    def link(m: re.Match[str]) -> str:
        label, url = m.group("text").strip(), m.group("url")
        if not keep_link_urls:
            return label
        if not label or label == url:
            return url
        return f"{label} ({url})"

    text = _LINK_RE.sub(link, text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^([ \t]*)[*+][ \t]+", r"\1- ", text, flags=re.MULTILINE)
    text = "\n".join(_strip_inline(line) if line.strip() else "" for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _looks_like_question(text: str) -> bool:
    """heuristic (รอคำตอบ: API ไม่บอก kind เอง): มีบรรทัดที่ลงท้ายด้วย ? / ？ = ถามกลับ

    ใช้ "บรรทัดไหนก็ได้" ไม่ใช่ "ท้ายข้อความ": คำถามกลับจริง (fixture 03_vague) ถามในย่อหน้าแรก
    แล้วต่อด้วยย่อหน้าชวนพิมพ์ที่ไม่มี ? — เสี่ยง: คำตอบที่ปิดด้วยคำถามชวนคุยจะนับเป็น clarify (นับ clarify_streak)
    """
    return any(line.rstrip().endswith(_QUESTION_MARKS) for line in text.split("\n"))


class MarkdownParser:
    """คำตอบของ gateway แบบ OpenAI chat.completion ที่เนื้อหาเป็น markdown (วัดจริง 2026-10-23, D-011)

    รูปแบบ content ที่เห็น: ย่อหน้าเกริ่น (มีหรือไม่มี) -> "## Product List" -> ก้อนละสินค้า
        ### N. <ชื่อ>
        ![alt](https://statice.homepro.co.th/...jpg)      (บางก้อน)
        - Brand: X / - Price: ฿14,999.00 / - Details: ... / - Product code: 1289220
        - Product link: [View product details](https://www.homepro.co.th/p/1289220)
    บางครั้งมีข้อความต่อท้ายรายการ; คำถามทั่วไปตอบเป็นข้อความไทยล้วน (มี **ตัวหนา** / รายการเลข)

    - คำตอบมักถูกตัดกลางสินค้า (finish_reason ยังเป็น "stop") -> รับเฉพาะก้อนที่ครบ:
      ชื่อ + Product code (-> sku) + Price (-> Decimal) + Product link ที่เป็น https; ก้อนไม่ครบทิ้ง (log จำนวน)
      บรรทัดสุดท้ายของ content (ไม่มี newline ปิด) อาจถูกตัดกลางบรรทัด -> นับเฉพาะลิงก์ markdown ที่มี ")" ปิด
    - sku ซ้ำ -> เก็บตัวแรก (validate ถือว่า sku ซ้ำทำให้ทั้งชุดไม่ผ่าน)
    - image_url จากบรรทัด ![..](https://..) ในก้อน (https เท่านั้น ไม่งั้น None)
    - kind: มีสินค้าครบ >=1 -> products; ไม่งั้นมีบรรทัดลงท้าย ? -> clarify; ไม่งั้น answer
      (heuristic — รอคำตอบ: API ไม่บอก kind เอง และไม่มีสัญญาณ cannot_answer)
    - intro = ข้อความก่อน "## Product List" (ไม่มีรายการ = ทั้งข้อความ) แปลงด้วย to_line_text
      มีสินค้า: ลิงก์เหลือแค่ข้อความ; ข้อความท้ายรายการไม่ใช้ (ลำดับที่ส่งคือ intro -> การ์ด)
      ไม่มีสินค้าครบ: ใช้ข้อความก่อน + หลังรายการ ลิงก์เป็น "ข้อความ (url)"; ว่าง -> AiBadResponse(parse)
    - clarify: clarify_question = ข้อความนั้น (intro = None)
    - escalate_hint = None, state_out = None: API ไม่มีฟิลด์แบบนี้ (ไม่คืน state — ความจำใช้แชทย้อนหลัง)
    - raw = content (markdown) ตัดความยาว; ซองผิด/ไม่มี choices/content ว่าง -> AiBadResponse(parse)
    - ข้อความ error ไม่ใส่เนื้อหาคำตอบ (อาจทวนข้อความลูกค้า)
    """

    def parse(self, body: bytes | str, *, latency_ms: int, attempts: int) -> AiResult:
        content, finish_reason = self._content(body, latency_ms, attempts)
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        raw = _truncate(content)

        m = _LIST_HEADING_RE.search(content)
        if m is None:
            before, list_part = content, None
        else:
            before, list_part = content[: m.start()], content[m.end() :]

        products: list[Product] = []
        trailing = ""
        if list_part is not None:
            blocks, trailing = self._blocks(list_part, ends_cleanly=content.endswith("\n"))
            seen: set[str] = set()
            dropped = 0
            for block in blocks:
                product = self._product(block)
                if product is None or product.sku in seen:
                    dropped += 1
                    continue
                seen.add(product.sku)
                products.append(product)
            log.info(
                "ai_markdown_products", kept=len(products), dropped=dropped, finish_reason=finish_reason
            )

        if products:
            return AiResult(
                kind=AiKind.PRODUCTS,
                intro=to_line_text(before, keep_link_urls=False) or None,
                products=products,
                raw=raw,
                latency_ms=latency_ms,
                attempts=attempts,
            )

        text = to_line_text(
            "\n\n".join(p for p in (before, trailing) if p.strip()), keep_link_urls=True
        )
        if not text:
            raise _bad("คำตอบไม่มีข้อความหรือสินค้าที่ครบ", latency_ms, attempts)
        if _looks_like_question(text):
            return AiResult(
                kind=AiKind.CLARIFY,
                clarify_question=text,
                raw=raw,
                latency_ms=latency_ms,
                attempts=attempts,
            )
        return AiResult(
            kind=AiKind.ANSWER, intro=text, raw=raw, latency_ms=latency_ms, attempts=attempts
        )

    @staticmethod
    def _content(body: bytes | str, latency_ms: int, attempts: int) -> tuple[str, str | None]:
        """ซอง chat.completion -> (choices[0].message.content, finish_reason)"""
        try:
            text = _to_text(body)
        except UnicodeDecodeError as e:
            raise _bad("คำตอบไม่ใช่ UTF-8", latency_ms, attempts) from e
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError as e:
            raise _bad("คำตอบไม่ใช่ JSON", latency_ms, attempts) from e
        if not isinstance(data, dict):
            raise _bad("คำตอบ JSON ไม่ใช่ object", latency_ms, attempts)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _bad("คำตอบไม่มี choices", latency_ms, attempts)
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise _bad("คำตอบไม่มี message.content", latency_ms, attempts)
        finish = choices[0].get("finish_reason")
        return content, finish if isinstance(finish, str) else None

    @staticmethod
    def _blocks(list_part: str, *, ends_cleanly: bool) -> tuple[list[_Block], str]:
        """แยกก้อนสินค้า -> (ก้อนทั้งหมด, ข้อความที่ไม่ใช่ส่วนของก้อน)"""
        lines = list_part.split("\n")
        last_index = len(lines) - 1
        blocks: list[_Block] = []
        other: list[str] = []
        current: _Block | None = None
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            # บรรทัดสุดท้ายที่ไม่มี newline ปิดอาจถูกตัดกลางบรรทัด
            maybe_cut = i == last_index and not ends_cleanly
            heading = _PRODUCT_HEADING_RE.match(line)
            if heading is not None:
                current = _Block(name=_strip_inline(heading.group("name")).strip())
                blocks.append(current)
                continue
            if current is None:
                other.append(line)
                continue
            image = _IMAGE_RE.fullmatch(line.strip())
            if image is not None:
                if not maybe_cut:
                    current.image_url = _https_url(image.group("url"))
                continue
            fm = _FIELD_RE.match(line)
            if fm is None:
                if maybe_cut or _BULLET_RE.match(line):
                    # บรรทัดของก้อนที่อ่านไม่ออก/ถูกตัด เช่น "- Product co" — ไม่ใช่ข้อความให้ลูกค้า
                    continue
                # ข้อความทั่วไปหลังก้อน (เช่น ปิดท้ายรายการ) — ไม่ใช่ส่วนของสินค้า
                other.append(line)
                current = None
                continue
            key = " ".join(fm.group("key").lower().split())
            value = fm.group("value")
            if key in _KEY_LINK:
                link = _LINK_RE.search(value)
                if link is not None:  # มี ")" ปิด = ครบ แม้อยู่บรรทัดสุดท้าย
                    current.fields["link"] = link.group("url")
                elif not maybe_cut and (bare := _BARE_URL_RE.match(value.strip("<>"))) is not None:
                    current.fields["link"] = bare.group("url")
                continue
            if maybe_cut:
                continue
            if key in _KEY_SKU:
                current.fields["sku"] = _strip_inline(value).strip()
            elif key in _KEY_PRICE:
                current.fields["price"] = value
        return blocks, "\n".join(other)

    @staticmethod
    def _product(block: _Block) -> Product | None:
        """ก้อนที่ครบ -> Product; ไม่ครบ (ถูกตัด/ขาดช่อง) -> None"""
        sku = block.fields.get("sku", "")
        price = _parse_price(block.fields.get("price", ""))
        url = _https_url(block.fields.get("link"))
        if not block.name or not sku or any(c.isspace() for c in sku) or price is None or url is None:
            return None
        return Product(sku=sku, name=block.name, price=price, url=url, image_url=block.image_url)


__all__ = ["PARSE_ERROR_CLASS", "AiParser", "JsonParser", "MarkdownParser", "to_line_text"]

"""app/api/close.py

GET /close?token=: ลิงก์ใช้ครั้งเดียวให้พนักงานปิดเคสที่ส่งต่อ

รายละเอียด:
 - ลิงก์ส่งไปกับข้อความแจ้ง call center ตอนส่งต่อ (PUBLIC_BASE_URL + token)
 - token ใช้ได้ครั้งเดียว มีวันหมดอายุ ใช้แล้ว/หมดอายุ -> แจ้งว่าใช้ไม่ได้
 - เรียก pipeline.handoff.end_handoff(conv, reason='staff_closed')
 - end_handoff เป็น UPDATE แบบมีเงื่อนไข ถ้า timeout/ลูกค้ากดคืนไปก่อน -> แจ้งว่าเคสปิดแล้ว
 - (เสนอ) หน้าเดียวกันให้พนักงานติด staff_label: ส่งต่อถูกต้อง / ส่งต่อเกิน / ลูกค้าขอเอง

ข้อตัดสินในงาน 8:
 - GET /close?token= ไม่มีผลข้างเคียง: แค่แสดงหน้ายืนยัน "ลิงก์นี้ใช้ปิดเคส" + ปุ่ม (ฟอร์ม POST, token ในช่อง hidden)
   ไม่บอกว่า token ใช้ได้ไหม — กัน bot แสดงตัวอย่างลิงก์ (LINE/Slack/สแกนอีเมล) ปิดเคสและเผา token ก่อนพนักงานกด
 - POST /close (ฟอร์ม token=, application/x-www-form-urlencoded) = ปิดจริง: consume_close_token -> end_handoff
   (STAFF_CLOSED) -> push RESUMED -> หน้าผล; อ่าน body เองด้วย urllib (ไม่มี python-multipart) จำกัดขนาด body
 - end_handoff ใช้ handoff_started_at ของ token (ไม่ใช่ของแถวปัจจุบัน) -> ลิงก์ของรอบเก่าปิดรอบใหม่ไม่ได้
 - ปิดสำเร็จ -> push RESUMED หาลูกค้าผ่าน sendGuard (dedupe_key ผูกกับรอบส่งต่อ) ส่งไม่ได้ก็ยังตอบว่าปิดแล้ว
 - HTML: escape token, Cache-Control: no-store, Referrer-Policy: no-referrer, X-Robots-Tag: noindex; token ไม่ลง log
 - staff_label ยังไม่ทำ (เสนอ)

แนวคิดจาก: src/routes/close.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8) — ยังไม่มีฟอร์ม staff_label
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.pipeline import handoff, replies
from app.pipeline.ports import Deps
from app.pipeline.schemas import HandoffEndReason
from app.pipeline.steps.send import send

log = structlog.get_logger(__name__)

router = APIRouter()

MSG_CONFIRM = "ลิงก์นี้ใช้ปิดเคส"
LABEL_CONFIRM = "ปิดเคสและให้ลูกค้ากลับไปคุยกับ AI"
MSG_CLOSED = "ปิดเคสแล้ว"
MSG_INVALID = "ลิงก์ใช้ไม่ได้หรือถูกใช้ไปแล้ว"
MSG_ALREADY_BOT = "เคสนี้กลับไปคุยกับ AI แล้ว"

RESUMED_PURPOSE = "resumed"

# token จริงยาว ~43 ตัวอักษร (token_urlsafe(32)); ยาวเกินนี้ไม่ใช่ของเรา
MAX_TOKEN_CHARS = 256
# body ของฟอร์ม POST (Caddy จำกัดอีกชั้น)
MAX_FORM_BYTES = 4096

_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _html(title: str, inner: str, status_code: int) -> HTMLResponse:
    page = (
        '<!doctype html><html lang="th"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{html.escape(title)}</title></head>"
        '<body style="font-family:sans-serif;text-align:center;padding:3rem 1rem">'
        f"{inner}</body></html>"
    )
    return HTMLResponse(page, status_code=status_code, headers=_HEADERS)


def _page(message: str, status_code: int = 200) -> HTMLResponse:
    return _html(message, f'<h1 style="font-size:1.5rem">{html.escape(message)}</h1>', status_code)


def _confirm_page(token: str) -> HTMLResponse:
    inner = (
        f'<h1 style="font-size:1.5rem">{html.escape(MSG_CONFIRM)}</h1>'
        '<form method="post" action="/close">'
        f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
        '<button type="submit" style="font-size:1.1rem;padding:.6rem 1.2rem">'
        f"{html.escape(LABEL_CONFIRM)}</button></form>"
    )
    return _html(MSG_CONFIRM, inner, 200)


def resumed_dedupe_key(conversation_id: int, started_at: datetime) -> str:
    return f"out:handoff_closed:{conversation_id}:{started_at.isoformat()}"


@router.get("/close")
async def close_confirm(token: str | None = None) -> HTMLResponse:
    """หน้ายืนยัน — ไม่แตะ DB (ไม่ consume ไม่ตรวจ token)"""
    if not token or len(token) > MAX_TOKEN_CHARS:
        return _page(MSG_INVALID, 400)
    return _confirm_page(token)


async def _form_token(request: Request) -> str | None:
    body = await request.body()
    if len(body) > MAX_FORM_BYTES:
        return None
    try:
        form = parse_qs(body.decode("utf-8"), max_num_fields=10)
    except (UnicodeDecodeError, ValueError):
        return None
    values = form.get("token")
    return values[-1] if values else None


@router.post("/close")
async def close_case(request: Request) -> HTMLResponse:
    """ปิดเคสจริง (ฟอร์มจากหน้ายืนยัน)"""
    state: Any = request.app.state
    deps: Deps = state.deps
    token = await _form_token(request)
    if not token or len(token) > MAX_TOKEN_CHARS:
        return _page(MSG_INVALID, 400)

    used = await deps.conversations.consume_close_token(token)
    if used is None:
        log.info("close_token_invalid")
        return _page(MSG_INVALID, 400)

    conv = await deps.conversations.get(used.conversation_id)
    if conv is None:
        return _page(MSG_INVALID, 400)
    # รอบที่ token ผูกไว้ ไม่ใช่รอบปัจจุบันของแถว
    conv_round = conv.model_copy(update={"handoff_started_at": used.handoff_started_at})
    new = await handoff.end_handoff(deps, conv_round, HandoffEndReason.STAFF_CLOSED)
    if new is None:
        log.info("close_already_ended", conversation_id=conv.id)
        return _page(MSG_ALREADY_BOT)

    try:
        line_user_id = await deps.users.get_line_user_id(conv.user_id)
        if line_user_id is None:
            log.warning("close_no_line_user", conversation_id=conv.id)
        else:
            result = await send(
                deps,
                conversation_id=conv.id,
                line_user_id=line_user_id,
                raw_event_id=None,
                outbound=replies.outbound_text(RESUMED_PURPOSE, replies.RESUMED),
                dedupe_key=resumed_dedupe_key(conv.id, used.handoff_started_at),
                text_redacted=replies.RESUMED,
            )
            log.info("close_resumed_sent", conversation_id=conv.id, send=result.status.value)
    except Exception:
        # สถานะใน DB เปลี่ยนแล้ว (กลับเป็น bot) -> หน้าเว็บยังบอกว่าปิดแล้ว
        log.exception("close_resumed_send_failed", conversation_id=conv.id)
    return _page(MSG_CLOSED)


__all__ = ["MSG_ALREADY_BOT", "MSG_CLOSED", "MSG_CONFIRM", "MSG_INVALID", "router"]

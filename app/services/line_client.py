"""app/services/line_client.py

client ของ LINE Messaging API: ที่เดียวที่ถือ channel access token

รายละเอียด:
 - reply(reply_token, messages), push(user_id, messages)
 - show_loading(user_id, seconds): loading animation ระหว่างรอ AI
 - ดึงเนื้อหารูปจาก LINE (มี timeout)
 - ใช้ line-bot-sdk หรือ httpx async
 - token ห้ามส่งให้ AI API ห้ามลง log
 - แยก error ที่ retry ได้ (5xx, timeout) กับไม่ได้ (4xx, replyToken หมดอายุ)
 - pipeline เรียกผ่าน steps/send เท่านั้น

แนวคิดจาก: src/line/lineApi.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว — HttpLineClient (httpx, ตาม docs/PLAN.md ไม่ใช้ line-bot-sdk; show_loading = start_loading ตาม ports)
       + verify_signature
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
from urllib.parse import quote

import httpx
import structlog

from app.config import Settings
from app.pipeline.schemas import JsonDict, LineApiError, MediaContent

log = structlog.get_logger(__name__)

API_BASE = "https://api.line.me"
DATA_API_BASE = "https://api-data.line.me"
# (เสนอ) timeout ของการเรียก LINE ทั่วไป / ดึงไฟล์ (ไฟล์ใหญ่กว่า)
LINE_TIMEOUT_S = 10.0
LINE_CONTENT_TIMEOUT_S = 20.0
# loading animation: LINE รับ 5–60 วินาที ทีละ 5
LOADING_MIN_S = 5
LOADING_MAX_S = 60
LOADING_STEP_S = 5
# ตัดข้อความ error จาก LINE ให้สั้น (ไม่เก็บ body ทั้งก้อน)
_ERROR_MESSAGE_MAX_CHARS = 200


def verify_signature(channel_secret: str, body: bytes, signature_header: str | None) -> bool:
    """ตรวจ X-Line-Signature = base64(HMAC-SHA256(channel_secret, body)); ไม่มี/ว่าง -> False

    body ต้องเป็น bytes ดิบตามที่รับมา (ห้าม parse แล้ว dump ใหม่)
    """
    if not signature_header or not channel_secret:
        return False
    expected = base64.b64encode(
        hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    )
    return hmac.compare_digest(expected, signature_header.strip().encode("utf-8"))


def loading_seconds(seconds: int) -> int:
    """ปัดขึ้นเป็นทีละ 5 แล้วจำกัดให้อยู่ใน 5–60 (ค่าที่ LINE รับ)"""
    rounded = math.ceil(max(seconds, 0) / LOADING_STEP_S) * LOADING_STEP_S
    return min(LOADING_MAX_S, max(LOADING_MIN_S, rounded))


def _is_retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def _error_from_response(op: str, resp: httpx.Response) -> LineApiError:
    # body ของ LINE เป็น {"message": "...", "details": [...]} — เอาแค่ message สั้นๆ (ไม่มี token)
    detail = ""
    try:
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            detail = data["message"][:_ERROR_MESSAGE_MAX_CHARS]
    except ValueError:
        pass
    status = resp.status_code
    return LineApiError(
        f"LINE {op} HTTP {status}" + (f": {detail}" if detail else ""),
        status_code=status,
        retryable=_is_retryable_status(status),
    )


class HttpLineClient:
    """LINE Messaging API ผ่าน httpx (implement ports.LineClient)

    - token อยู่ใน header Authorization เท่านั้น ห้ามลง log
    - timeout ทุกการเรียก; timeout / ต่อไม่ได้ -> LineApiError(status_code=None, retryable=True)
    - 429 / 5xx -> retryable=True; 4xx อื่น (รวม replyToken ใช้ไม่ได้ = 400) -> retryable=False
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._token = settings.line_channel_access_token
        self._owns_http = http is None
        self._http = http if http is not None else httpx.AsyncClient()

    async def aclose(self) -> None:
        """ปิด httpx client ถ้าสร้างเอง"""
        if self._owns_http:
            await self._http.aclose()

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token.get_secret_value()}"}
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self,
        op: str,
        method: str,
        url: str,
        *,
        json: JsonDict | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_s: float = LINE_TIMEOUT_S,
    ) -> httpx.Response:
        try:
            resp = await self._http.request(
                method,
                url,
                json=json,
                headers=self._headers(extra_headers),
                timeout=httpx.Timeout(timeout_s),
            )
        except httpx.TimeoutException:
            log.warning("line_api_error", op=op, error_class="timeout")
            raise LineApiError(f"LINE {op} timeout", status_code=None, retryable=True) from None
        except httpx.TransportError:
            log.warning("line_api_error", op=op, error_class="connect")
            raise LineApiError(f"LINE {op} ต่อไม่ได้", status_code=None, retryable=True) from None
        if resp.status_code >= 400:
            err = _error_from_response(op, resp)
            log.warning("line_api_error", op=op, status_code=resp.status_code, retryable=err.retryable)
            raise err
        return resp

    async def reply(self, reply_token: str, messages: list[JsonDict]) -> None:
        await self._request(
            "reply",
            "POST",
            f"{API_BASE}/v2/bot/message/reply",
            json={"replyToken": reply_token, "messages": messages},
        )

    async def push(
        self, line_user_id: str, messages: list[JsonDict], *, retry_key: str | None = None
    ) -> None:
        # X-Line-Retry-Key: LINE ไม่ส่งซ้ำถ้า key เดิม (ตอบ 409 = ส่งไปแล้ว — ผู้เรียกตัดสินเอง)
        extra = {"X-Line-Retry-Key": retry_key} if retry_key else None
        await self._request(
            "push",
            "POST",
            f"{API_BASE}/v2/bot/message/push",
            json={"to": line_user_id, "messages": messages},
            extra_headers=extra,
        )

    async def start_loading(self, line_user_id: str, seconds: int = 20) -> None:
        """loading animation — พังไม่เป็นไร: log แล้วกลืน error (ตามสัญญา ports)"""
        try:
            await self._request(
                "start_loading",
                "POST",
                f"{API_BASE}/v2/bot/chat/loading/start",
                json={"chatId": line_user_id, "loadingSeconds": loading_seconds(seconds)},
            )
        except LineApiError:
            # _request log ไว้แล้ว
            return

    async def get_content(self, line_message_id: str) -> MediaContent:
        resp = await self._request(
            "get_content",
            "GET",
            f"{DATA_API_BASE}/v2/bot/message/{quote(line_message_id, safe='')}/content",
            timeout_s=LINE_CONTENT_TIMEOUT_S,
        )
        content_type = resp.headers.get("content-type") or "application/octet-stream"
        return MediaContent(data=resp.content, content_type=content_type)


__all__ = [
    "API_BASE",
    "DATA_API_BASE",
    "LINE_CONTENT_TIMEOUT_S",
    "LINE_TIMEOUT_S",
    "HttpLineClient",
    "loading_seconds",
    "verify_signature",
]

"""tests/services/test_line_client.py

เคสทดสอบของ app/services/line_client.py (จำลอง LINE ด้วย respx)

รายละเอียด:
 - reply / push / start_loading / get_content: endpoint, payload, header (Bearer token, X-Line-Retry-Key)
 - ทุกการเรียกมี timeout
 - error: 429/5xx/timeout/ต่อไม่ได้ -> retryable=True; 4xx อื่น (replyToken ใช้ไม่ได้ = 400) -> retryable=False
 - start_loading ปัดวินาทีเป็นทีละ 5 ในช่วง 5–60 และกลืน error
 - token ไม่ลง log
 - verify_signature: vector ที่รู้ค่า, ลายเซ็นผิด, None/ว่าง -> False

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest
import respx

from app.pipeline.schemas import LineApiError, MediaContent
from app.services import line_client as line_client_mod
from app.services.line_client import (
    LINE_CONTENT_TIMEOUT_S,
    LINE_TIMEOUT_S,
    HttpLineClient,
    loading_seconds,
    verify_signature,
)
from tests.fakes import make_settings
from tests.services._helpers import RecordingLogger

TOKEN = "line-token-abc"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
PUSH_URL = "https://api.line.me/v2/bot/message/push"
LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"
MESSAGES = [{"type": "text", "text": "สวัสดีค่ะ"}]


@pytest.fixture
def logger(monkeypatch) -> RecordingLogger:
    rec = RecordingLogger()
    monkeypatch.setattr(line_client_mod, "log", rec)
    return rec


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as http:
        yield HttpLineClient(make_settings(line_channel_access_token=TOKEN), http=http)


def body(call) -> dict:
    return json.loads(call.request.content)


def timeout_of(call) -> dict:
    return call.request.extensions["timeout"]


# --- reply ---


@respx.mock
async def test_reply_endpoint_payload_headers(client, logger):
    route = respx.post(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    await client.reply("rt-1", MESSAGES)
    call = route.calls[0]
    assert body(call) == {"replyToken": "rt-1", "messages": MESSAGES}
    assert call.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert call.request.headers["content-type"] == "application/json"
    assert timeout_of(call)["read"] == LINE_TIMEOUT_S


@respx.mock
async def test_reply_invalid_token_is_400_not_retryable(client, logger):
    respx.post(REPLY_URL).mock(
        return_value=httpx.Response(400, json={"message": "Invalid reply token"})
    )
    with pytest.raises(LineApiError) as ei:
        await client.reply("expired", MESSAGES)
    assert (ei.value.status_code, ei.value.retryable) == (400, False)
    assert "Invalid reply token" in str(ei.value)


# --- push ---


@respx.mock
async def test_push_with_retry_key(client, logger):
    route = respx.post(PUSH_URL).mock(return_value=httpx.Response(200, json={}))
    key = "123e4567-e89b-12d3-a456-426614174000"
    await client.push("U123", MESSAGES, retry_key=key)
    call = route.calls[0]
    assert body(call) == {"to": "U123", "messages": MESSAGES}
    assert call.request.headers["x-line-retry-key"] == key
    assert call.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert timeout_of(call)["read"] == LINE_TIMEOUT_S


@respx.mock
async def test_push_without_retry_key_has_no_header(client, logger):
    route = respx.post(PUSH_URL).mock(return_value=httpx.Response(200, json={}))
    await client.push("U123", MESSAGES)
    assert "x-line-retry-key" not in route.calls[0].request.headers


# --- error classes ---


@respx.mock
@pytest.mark.parametrize(
    ("status", "retryable"),
    [(400, False), (401, False), (403, False), (404, False), (409, False),
     (429, True), (500, True), (502, True), (503, True)],
)
async def test_status_error_classes(client, logger, status, retryable):
    respx.post(PUSH_URL).mock(return_value=httpx.Response(status, json={"message": "x"}))
    with pytest.raises(LineApiError) as ei:
        await client.push("U123", MESSAGES)
    assert ei.value.status_code == status
    assert ei.value.retryable is retryable


@respx.mock
@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("t"), httpx.ConnectTimeout("t"), httpx.ConnectError("refused"),
            httpx.RemoteProtocolError("bye")]
)
async def test_network_errors_are_retryable(client, logger, exc):
    respx.post(PUSH_URL).mock(side_effect=exc)
    with pytest.raises(LineApiError) as ei:
        await client.push("U123", MESSAGES)
    assert ei.value.status_code is None
    assert ei.value.retryable is True


@respx.mock
async def test_error_with_non_json_body(client, logger):
    respx.post(REPLY_URL).mock(return_value=httpx.Response(502, content=b"<html>bad gateway</html>"))
    with pytest.raises(LineApiError) as ei:
        await client.reply("rt", MESSAGES)
    assert ei.value.retryable is True
    assert "<html>" not in str(ei.value)


@respx.mock
async def test_token_never_logged(client, logger):
    respx.post(PUSH_URL).mock(side_effect=[httpx.Response(500), httpx.ConnectError("x")])
    for _ in range(2):
        with pytest.raises(LineApiError):
            await client.push("U123", MESSAGES)
    assert logger.records
    assert TOKEN not in logger.dump()
    assert "สวัสดีค่ะ" not in logger.dump()


# --- start_loading ---


@respx.mock
async def test_start_loading_payload(client, logger):
    route = respx.post(LOADING_URL).mock(return_value=httpx.Response(202, json={}))
    await client.start_loading("U123", 20)
    call = route.calls[0]
    assert body(call) == {"chatId": "U123", "loadingSeconds": 20}
    assert call.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert timeout_of(call)["read"] == LINE_TIMEOUT_S


@respx.mock
async def test_start_loading_swallows_errors(client, logger):
    respx.post(LOADING_URL).mock(side_effect=[httpx.Response(500), httpx.ConnectError("x")])
    await client.start_loading("U123", 20)  # ไม่ยก error
    await client.start_loading("U123", 20)
    assert logger.events().count("line_api_error") == 2


@pytest.mark.parametrize(
    ("given", "expected"),
    [(-5, 5), (0, 5), (1, 5), (5, 5), (6, 10), (20, 20), (21, 25), (59, 60), (60, 60), (61, 60), (999, 60)],
)
def test_loading_seconds_rounds_up_to_step_and_clamps(given, expected):
    assert loading_seconds(given) == expected


# --- get_content ---


@respx.mock
async def test_get_content(client, logger):
    route = respx.get("https://api-data.line.me/v2/bot/message/1234567/content").mock(
        return_value=httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
    )
    content = await client.get_content("1234567")
    assert content == MediaContent(data=b"\xff\xd8jpeg", content_type="image/jpeg")
    call = route.calls[0]
    assert call.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert timeout_of(call)["read"] == LINE_CONTENT_TIMEOUT_S


@respx.mock
async def test_get_content_missing_content_type(client, logger):
    respx.get("https://api-data.line.me/v2/bot/message/1/content").mock(
        return_value=httpx.Response(200, content=b"x", headers={"content-type": ""})
    )
    assert (await client.get_content("1")).content_type == "application/octet-stream"


@respx.mock
async def test_get_content_quotes_message_id(client, logger):
    route = respx.get("https://api-data.line.me/v2/bot/message/..%2Fx/content").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    await client.get_content("../x")
    assert route.call_count == 1


@respx.mock
async def test_get_content_errors(client, logger):
    respx.get("https://api-data.line.me/v2/bot/message/9/content").mock(
        side_effect=[httpx.Response(404, json={"message": "Not found"}), httpx.ReadTimeout("t")]
    )
    with pytest.raises(LineApiError) as ei:
        await client.get_content("9")
    assert (ei.value.status_code, ei.value.retryable) == (404, False)
    with pytest.raises(LineApiError) as ei:
        await client.get_content("9")
    assert (ei.value.status_code, ei.value.retryable) == (None, True)


# --- verify_signature ---


def _sign(secret: str, raw: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()


def test_verify_signature_known_vector():
    # HMAC-SHA256("key", "The quick brown fox jumps over the lazy dog")
    # = f7bc83f4...2d1a3cd8 (vector ที่รู้ค่า) -> base64
    expected_hex = "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    sig = base64.b64encode(bytes.fromhex(expected_hex)).decode()
    assert sig == "97yD9DBThCSxMpjmqm+xQ+9NWaFJRhdZl0edvC0aPNg="
    assert verify_signature("key", b"The quick brown fox jumps over the lazy dog", sig)


def test_verify_signature_line_body_computed_in_test():
    secret = "test-channel-secret"
    raw = '{"destination":"U1","events":[{"type":"message","message":{"text":"ตู้เย็น"}}]}'.encode()
    sig = _sign(secret, raw)
    assert verify_signature(secret, raw, sig)
    assert verify_signature(secret, raw, f"  {sig} ")  # ช่องว่างรอบ header


@pytest.mark.parametrize("header", [None, ""])
def test_verify_signature_missing_header(header):
    assert verify_signature("secret", b"{}", header) is False


def test_verify_signature_rejects_wrong_signature_body_or_secret():
    raw = b'{"events":[]}'
    sig = _sign("secret", raw)
    assert not verify_signature("secret", raw + b" ", sig)  # body เปลี่ยน
    assert not verify_signature("other", raw, sig)  # secret ผิด
    assert not verify_signature("secret", raw, sig[:-2] + "AA")
    assert not verify_signature("secret", raw, "ไม่ใช่ base64")
    assert not verify_signature("", raw, _sign("", raw))  # secret ว่าง = ไม่ยอม

"""app/logging.py

ตั้ง structlog ให้ log เป็น JSON และติด id ตามย้อนได้ทั้งสาย

รายละเอียด:
 - ทุก log ในงานหนึ่งติด raw_event_id และ ai_run_id (ผ่าน contextvars)
 - id ชุดเดียวกันอยู่ใน ai_steps และข้อความ error ที่ส่ง Sentry
 - ผ่าน privacy/redact ก่อนเขียน: ห้ามมีเบอร์โทร, เลขบัตร, token, replyToken
 - ระดับ log จาก LOG_LEVEL

แนวคิดจาก: src/observability/logger.js, src/observability/redact.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (configure_logging, bind_ids, processor ตัด secret + redact)
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[redacted]"
_REDACT_FAILED = "[redact-failed]"
_SENSITIVE_KEY_PARTS = ("token", "secret", "key")
_SENSITIVE_KEYS = {"reply_token", "replytoken"}
_MAX_DEPTH = 6
# key ที่ structlog ใช้เอง ไม่ต้อง redact
_STRUCTLOG_KEYS = {"level", "timestamp", "logger"}


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    k = key.casefold()
    return k in _SENSITIVE_KEYS or any(part in k for part in _SENSITIVE_KEY_PARTS)


def _get_redact_text() -> Callable[[str], str] | None:
    # import ตอนเรียก ไม่ใช่ตอนโหลดโมดูล: app/privacy/redact.py เขียนโดยงานอื่นคู่ขนาน
    # (ยังอาจไม่มี redact_text) และกัน import วนถ้า redact อยาก log เอง
    # ไม่มี -> คืน None แล้วผ่านค่าไปตรงๆ (ส่วนตัด key ที่เป็น secret ยังทำงาน)
    try:
        from app.privacy.redact import redact_text
    except ImportError:
        return None
    return redact_text


def _scrub(value: Any, redact: Callable[[str], str] | None, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return value
    if isinstance(value, str):
        if redact is None:
            return value
        try:
            return redact(value)
        except Exception:
            # redact พังห้ามทำให้ log พัง และห้ามปล่อยข้อความดิบออกไป
            return _REDACT_FAILED
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if _is_sensitive_key(k) else _scrub(v, redact, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_scrub(v, redact, depth + 1) for v in value]
    return value


def scrub_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """processor ของ structlog: key ที่ชื่อเหมือน secret -> "[redacted]", ค่า string ผ่าน redact_text"""
    redact = _get_redact_text()
    for k in list(event_dict.keys()):
        if k in _STRUCTLOG_KEYS:
            continue
        if _is_sensitive_key(k):
            event_dict[k] = REDACTED
        else:
            event_dict[k] = _scrub(event_dict[k], redact)
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """ตั้ง structlog ให้เขียน JSON บรรทัดละหนึ่ง event ไป stdout ที่ระดับ `level`"""
    level_no = logging.getLevelName(level.upper())
    if not isinstance(level_no, int):
        raise ValueError(f"LOG_LEVEL ไม่รู้จัก: {level}")
    # log ของไลบรารี (uvicorn, httpx) เคารพระดับเดียวกัน
    logging.basicConfig(level=level_no, stream=sys.stdout, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            # แปลง exception เป็น string ก่อน scrub เพื่อให้ traceback ผ่าน redact ด้วย
            structlog.processors.format_exc_info,
            scrub_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def bind_ids(raw_event_id: int | None = None, ai_run_id: int | None = None) -> None:
    """ติด raw_event_id / ai_run_id กับทุก log ใน context ปัจจุบัน (ค่า None = ไม่แตะของเดิม)"""
    ids = {k: v for k, v in (("raw_event_id", raw_event_id), ("ai_run_id", ai_run_id)) if v is not None}
    if ids:
        structlog.contextvars.bind_contextvars(**ids)


def clear_ids() -> None:
    """ล้าง context ของ log (เรียกตอนเริ่ม/จบแต่ละงาน)"""
    structlog.contextvars.clear_contextvars()

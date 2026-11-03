"""tests/services/_helpers.py

ตัวช่วยของเทสต์ services: เวลาปลอม (monotonic + sleep) และ logger ที่บันทึกการเรียก

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

from typing import Any


class FakeTime:
    """monotonic + sleep ปลอม: sleep เลื่อนเวลาทันทีไม่รอจริง"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingLogger:
    """แทน structlog logger ของโมดูล (monkeypatch) เก็บ (level, event, kwargs) ไว้ตรวจ"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **kw: Any) -> None:
        self.records.append((level, event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self._record("debug", event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        self._record("info", event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._record("warning", event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        self._record("error", event, **kw)

    def exception(self, event: str, **kw: Any) -> None:
        self._record("exception", event, **kw)

    def events(self) -> list[str]:
        return [e for _, e, _ in self.records]

    def dump(self) -> str:
        """ทุกอย่างที่ log ไปเป็น string เดียว (ใช้ตรวจว่าไม่มีความลับ/ข้อความดิบ)"""
        return repr(self.records)

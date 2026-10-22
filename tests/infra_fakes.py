"""tests/infra_fakes.py

fake ของโครงสร้างพื้นฐานที่ไม่อยู่ในพอร์ตของ pipeline: Redis (SET NX PX / GETDEL / EVAL ล็อก), arq pool,
orchestrator.run_turn ปลอม และ user lookup — ใช้ในเทสต์ api / worker / services (งาน 8)

รายละเอียด:
 - FakeRedis: เก็บในหน่วยความจำ หมดอายุตาม time.monotonic จริง (เทสต์ล็อกใช้ TTL ระดับมิลลิวินาที)
   eval รู้จักแค่ RELEASE_SCRIPT / RENEW_SCRIPT ของ app/worker/locks.py
 - FakeArqPool: ไม่รับ job id ซ้ำจนกว่าจะ finish(job_id) (= keep_result=0 ของ arq)
 - FakeTurns: แทน orchestrator.run_turn — บันทึกลำดับ raw id, ยก error ตามที่ตั้งต่อ id

แนวคิดจาก: ใหม่ (งาน 8)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.orchestrator import TurnOutcome
from app.pipeline.schemas import RawEvent
from app.worker.locks import RELEASE_SCRIPT, RENEW_SCRIPT


def _b(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


class FakeRedis:
    """ส่วนของ redis.asyncio.Redis ที่โค้ดเราใช้"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, float | None]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.ping_ok = True

    def _get(self, key: str) -> bytes | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def pttl_ms(self, key: str) -> float | None:
        """ms ที่เหลือ (None = ไม่มี key หรือไม่มี TTL)"""
        if self._get(key) is None:
            return None
        expires_at = self._store[key][1]
        return None if expires_at is None else (expires_at - time.monotonic()) * 1000

    async def set(
        self,
        name: str,
        value: Any,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        self.calls.append(("set", (name, ex, px, nx)))
        if nx and self._get(name) is not None:
            return None
        ttl_s = ex if ex is not None else (px / 1000 if px is not None else None)
        self._store[name] = (_b(value), None if ttl_s is None else time.monotonic() + ttl_s)
        return True

    async def get(self, name: str) -> bytes | None:
        return self._get(name)

    async def getdel(self, name: str) -> bytes | None:
        self.calls.append(("getdel", (name,)))
        value = self._get(name)
        self._store.pop(name, None)
        return value

    async def pexpire(self, name: str, ms: int) -> bool:
        value = self._get(name)
        if value is None:
            return False
        self._store[name] = (value, time.monotonic() + int(ms) / 1000)
        return True

    async def delete(self, name: str) -> int:
        existed = self._get(name) is not None
        self._store.pop(name, None)
        return int(existed)

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        self.calls.append(("eval", (script, numkeys, *keys_and_args)))
        keys, args = keys_and_args[:numkeys], keys_and_args[numkeys:]
        key = keys[0]
        owner = self._get(key) == _b(args[0])
        if script == RELEASE_SCRIPT:
            return await self.delete(key) if owner else 0
        if script == RENEW_SCRIPT:
            return int(await self.pexpire(key, int(args[1]))) if owner else 0
        raise NotImplementedError("FakeRedis.eval รู้จักแค่สคริปต์ของ app/worker/locks.py")

    async def ping(self) -> bool:
        if not self.ping_ok:
            raise ConnectionError("redis down")
        return True

    async def aclose(self) -> None:
        self.calls.append(("aclose", ()))

    def count(self, op: str) -> int:
        return sum(1 for name, _ in self.calls if name == op)


class FakeArqPool:
    """enqueue_job แบบ arq: job id ซ้ำ (ยังไม่ finish) -> None"""

    def __init__(self, *, fail: bool = False) -> None:
        self.jobs: dict[str, tuple[str, tuple[Any, ...]]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], str | None]] = []
        self.fail = fail

    async def enqueue_job(
        self, function: str, *args: Any, _job_id: str | None = None, **kwargs: Any
    ) -> object | None:
        self.calls.append((function, args, _job_id))
        if self.fail:
            raise ConnectionError("redis down")
        job_id = _job_id or f"auto-{len(self.calls)}"
        if job_id in self.jobs:
            return None
        self.jobs[job_id] = (function, args)
        return object()

    def finish(self, job_id: str) -> None:
        """งานจบ (keep_result=0 -> ไม่มีผลค้าง ใส่ id เดิมได้อีก)"""
        self.jobs.pop(job_id, None)


@dataclass
class FakeTurns:
    """แทน orchestrator.run_turn: errors[raw_id] = รายการ Exception ที่ยกทีละครั้ง"""

    calls: list[int] = field(default_factory=list)
    errors: dict[int, list[Exception]] = field(default_factory=dict)
    outcome: TurnOutcome = field(default_factory=lambda: TurnOutcome(route="ai_answer"))

    async def __call__(self, deps: Any, raw: RawEvent, cfg: Any) -> Any:
        self.calls.append(raw.id)
        queue = self.errors.get(raw.id)
        if queue:
            raise queue.pop(0)
        return self.outcome


class FakeUserLookup:
    def __init__(self, mapping: dict[int, str] | None = None) -> None:
        self.mapping = dict(mapping or {})

    async def line_user_id_for_conversation(self, conversation_id: int) -> str | None:
        return self.mapping.get(conversation_id)

"""app/worker/locks.py

ล็อกต่อ user ระดับ Worker ให้ข้อความของ user เดียวกันทำทีละอัน

รายละเอียด:
 - TTL ของล็อกต้องมากกว่าเพดานเวลาของ call_ai (TURN_DEADLINE_S)
 - ต่ออายุล็อกเป็นระยะระหว่างรอ AI (ไม่งั้นหมดอายุกลางทาง ข้อความถัดไปแซง)
 - ปล่อยล็อกเฉพาะเมื่อยังเป็นเจ้าของ (token ของล็อก)
 - ห้ามถือ transaction DB ค้างระหว่างรอ AI
 - คำสั่งเปลี่ยนสถานะไม่รอล็อกนี้ (ดู api/fast_path.py)
 - ที่เก็บล็อก: ตัดสินแล้วว่าใช้ Redis (docs/PLAN.md) ไม่ใช้ advisory lock ของ Postgres เพราะ
   advisory lock ผูกกับ session -> ต้องถือ connection ของ pool ค้างตลอดเวลารอ AI (ขัดหลัก "ไม่ถือ DB ค้าง"
   และกิน pool), ส่วน Redis มี TTL ในตัว worker ตายกลางทางล็อกหลุดเองไม่ต้องรอ TCP timeout
   ถ้า Redis ล่มคิวก็ล่มอยู่แล้ว จึงไม่เพิ่มจุดพังใหม่

แนวคิดจาก: src/pipeline/userQueue.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8) — SET NX PX + token สุ่ม, ปล่อย/ต่ออายุด้วย Lua compare-and-*, ต่ออายุทุก ~ttl/3
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from types import TracebackType
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

KEY_PREFIX = "lock:user:"
# รอล็อกแบบวนถามทุกเท่านี้วินาที
DEFAULT_POLL_S = 0.2
# ช่องเผื่อเหนือ TURN_DEADLINE_S (docs/PLAN.md: TTL = TURN_DEADLINE_S + 30 วิ)
LOCK_TTL_MARGIN_S = 30

# ปล่อยเฉพาะเมื่อยังเป็นเจ้าของ (กันลบล็อกของคนอื่นที่ได้ไปหลังของเราหมดอายุ)
RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# ต่ออายุเฉพาะเมื่อยังเป็นเจ้าของ
RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class _RedisLike(Protocol):
    """ส่วนของ redis.asyncio.Redis (หรือ ArqRedis) ที่ใช้ — เทสต์ใช้ fake"""

    async def set(
        self, name: str, value: str, *, nx: bool = False, px: int | None = None
    ) -> Any: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any: ...


def lock_ttl_ms(turn_deadline_s: float) -> int:
    """TTL ของล็อก = TURN_DEADLINE_S + 30 วิ (มิลลิวินาที)"""
    return int((turn_deadline_s + LOCK_TTL_MARGIN_S) * 1000)


def lock_key(line_user_id: str) -> str:
    return f"{KEY_PREFIX}{line_user_id}"


class HeldLock:
    """ล็อกที่ได้มาแล้ว — ใช้เป็น async context manager: เข้า = เริ่มต่ออายุเบื้องหลัง, ออก = หยุดต่ออายุแล้วปล่อย

    lost=True เมื่อต่ออายุแล้วพบว่าไม่ใช่เจ้าของแล้ว (หมดอายุไปก่อน) — log เตือน งานที่ทำอยู่ทำต่อ
    (sendGuard / claim แบบมีเงื่อนไขกันผลเสียหลักไว้แล้ว)
    """

    def __init__(self, redis: _RedisLike, key: str, token: str, ttl_ms: int) -> None:
        self._redis = redis
        self.key = key
        self.token = token
        self.ttl_ms = ttl_ms
        self.lost = False
        self._renew_task: asyncio.Task[None] | None = None
        self._released = False

    @property
    def renew_interval_s(self) -> float:
        return self.ttl_ms / 1000 / 3

    async def renew(self) -> bool:
        """ต่ออายุเป็น ttl_ms อีกรอบ; False = ไม่ใช่เจ้าของแล้ว"""
        ok = bool(await self._redis.eval(RENEW_SCRIPT, 1, self.key, self.token, self.ttl_ms))
        if not ok:
            self.lost = True
        return ok

    async def release(self) -> bool:
        """ปล่อยล็อกถ้ายังเป็นเจ้าของ (เรียกซ้ำได้) — False = ไม่ใช่เจ้าของแล้ว/ปล่อยไปแล้ว"""
        if self._released:
            return False
        self._released = True
        return bool(await self._redis.eval(RELEASE_SCRIPT, 1, self.key, self.token))

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self.renew_interval_s)
            try:
                if not await self.renew():
                    log.warning("user_lock_lost")
                    return
            except Exception:
                # Redis สะดุดชั่วคราว -> ลองรอบหน้า (TTL ยังเหลือ 2/3)
                log.exception("user_lock_renew_failed")

    async def __aenter__(self) -> HeldLock:
        self._renew_task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._renew_task is not None:
            self._renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renew_task
            self._renew_task = None
        try:
            released = await self.release()
        except Exception:
            # ปล่อยไม่ได้ -> ล็อกหมดอายุเองตาม TTL
            log.exception("user_lock_release_failed")
            return
        if not released and not self.lost:
            log.warning("user_lock_not_owner_on_release")


class UserLock:
    """ล็อกต่อ user บน Redis: lock:user:{line_user_id}"""

    def __init__(self, redis: _RedisLike, *, poll_s: float = DEFAULT_POLL_S) -> None:
        self._redis = redis
        self._poll_s = poll_s

    async def acquire(self, line_user_id: str, ttl_ms: int, wait_s: float) -> HeldLock | None:
        """SET key token NX PX ttl_ms วนถามจนได้หรือครบ wait_s วินาที; ไม่ได้ -> None

        ได้แล้วต้องใช้กับ `async with` (ต่ออายุ + ปล่อย) หรือเรียก release() เอง
        """
        if ttl_ms <= 0:
            raise ValueError("ttl_ms ต้องมากกว่า 0")
        key = lock_key(line_user_id)
        token = secrets.token_hex(16)
        deadline = time.monotonic() + max(wait_s, 0)
        while True:
            if await self._redis.set(key, token, nx=True, px=ttl_ms):
                return HeldLock(self._redis, key, token, ttl_ms)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(self._poll_s, remaining))


__all__ = [
    "KEY_PREFIX",
    "LOCK_TTL_MARGIN_S",
    "RELEASE_SCRIPT",
    "RENEW_SCRIPT",
    "HeldLock",
    "UserLock",
    "lock_key",
    "lock_ttl_ms",
]

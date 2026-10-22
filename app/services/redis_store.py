"""app/services/redis_store.py

replyToken ใน Redis (implement ports.ReplyTokens)

รายละเอียด:
 - key = replytoken:{raw_event_id} TTL = REPLY_TOKEN_TTL_S (docs/PLAN.md); ไม่ลง line_raw_events
 - put: SET key token EX ttl (webhook เรียกหลัง insert raw)
 - take: GETDEL — ใช้ได้ครั้งเดียว; None = ไม่มี/หมดอายุ/ใช้ไปแล้ว -> sendGuard ใช้ push
 - Redis เป็นคิว + ของชั่วคราวเท่านั้น ความจริงอยู่ที่ Postgres
 - replyToken ห้ามลง log

แนวคิดจาก: src/store/redisStore.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

from typing import Any, Protocol

KEY_PREFIX = "replytoken:"


class _RedisLike(Protocol):
    """ส่วนของ redis.asyncio.Redis (หรือ ArqRedis) ที่ใช้ — เทสต์ใช้ fake"""

    async def set(self, name: str, value: str, *, ex: int | None = None) -> Any: ...

    async def getdel(self, name: str) -> Any: ...


def reply_token_key(raw_event_id: int) -> str:
    return f"{KEY_PREFIX}{raw_event_id}"


class RedisReplyTokens:
    """ReplyTokens บน Redis"""

    def __init__(self, redis: _RedisLike, ttl_s: int) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s ต้องมากกว่า 0")
        self._redis = redis
        self._ttl_s = ttl_s

    async def put(self, raw_event_id: int, reply_token: str) -> None:
        await self._redis.set(reply_token_key(raw_event_id), reply_token, ex=self._ttl_s)

    async def take(self, raw_event_id: int) -> str | None:
        value = await self._redis.getdel(reply_token_key(raw_event_id))
        if value is None:
            return None
        # ArqRedis ไม่ได้ตั้ง decode_responses -> ได้ bytes
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return str(value) or None


__all__ = ["KEY_PREFIX", "RedisReplyTokens", "reply_token_key"]

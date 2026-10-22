"""tests/worker/test_locks.py — ล็อกต่อ user บน Redis (FakeRedis)"""

from __future__ import annotations

import asyncio

from app.worker.locks import RELEASE_SCRIPT, RENEW_SCRIPT, UserLock, lock_key, lock_ttl_ms
from tests.infra_fakes import FakeRedis


def test_lock_ttl_is_turn_deadline_plus_30s() -> None:
    assert lock_ttl_ms(50) == 80_000
    assert lock_key("U1") == "lock:user:U1"


async def test_acquire_sets_nx_px_with_random_token() -> None:
    redis = FakeRedis()
    lock = UserLock(redis)
    held = await lock.acquire("U1", ttl_ms=10_000, wait_s=0)
    assert held is not None
    assert await redis.get("lock:user:U1") == held.token.encode()
    assert ("set", ("lock:user:U1", None, 10_000, True)) in redis.calls
    other = await UserLock(redis).acquire("U2", ttl_ms=10_000, wait_s=0)
    assert other is not None and other.token != held.token


async def test_second_acquire_fails_while_held() -> None:
    redis = FakeRedis()
    held = await UserLock(redis).acquire("U1", ttl_ms=10_000, wait_s=0)
    assert held is not None
    assert await UserLock(redis, poll_s=0.01).acquire("U1", ttl_ms=10_000, wait_s=0.05) is None


async def test_acquire_waits_until_released() -> None:
    redis = FakeRedis()
    held = await UserLock(redis).acquire("U1", ttl_ms=10_000, wait_s=0)
    assert held is not None

    async def release_soon() -> None:
        await asyncio.sleep(0.05)
        await held.release()

    task = asyncio.create_task(release_soon())
    second = await UserLock(redis, poll_s=0.01).acquire("U1", ttl_ms=10_000, wait_s=1)
    await task
    assert second is not None


async def test_release_only_by_owner() -> None:
    redis = FakeRedis()
    held = await UserLock(redis).acquire("U1", ttl_ms=10_000, wait_s=0)
    assert held is not None
    # ล็อกหมดอายุแล้วคนอื่นได้ไป
    await redis.set("lock:user:U1", "someone-else")
    assert await held.release() is False
    assert await redis.get("lock:user:U1") == b"someone-else"
    assert any(c[0] == "eval" and c[1][0] == RELEASE_SCRIPT for c in redis.calls)


async def test_context_manager_renews_and_releases() -> None:
    redis = FakeRedis()
    held = await UserLock(redis).acquire("U1", ttl_ms=150, wait_s=0)
    assert held is not None
    async with held:
        # นานกว่า TTL เดิม (150ms) แต่ต่ออายุทุก ~50ms -> ยังถืออยู่
        await asyncio.sleep(0.4)
        assert await redis.get("lock:user:U1") == held.token.encode()
        task = held._renew_task
    renews = sum(1 for c in redis.calls if c[0] == "eval" and c[1][0] == RENEW_SCRIPT)
    assert renews >= 3
    assert await redis.get("lock:user:U1") is None
    assert task is not None and task.done()
    assert held.lost is False


async def test_lost_lock_is_flagged_and_not_deleted() -> None:
    redis = FakeRedis()
    held = await UserLock(redis).acquire("U1", ttl_ms=90, wait_s=0)
    assert held is not None
    async with held:
        await redis.set("lock:user:U1", "intruder")
        await asyncio.sleep(0.1)
        assert held.lost is True
    assert await redis.get("lock:user:U1") == b"intruder"


async def test_renewal_cancelled_on_exception() -> None:
    redis = FakeRedis()
    held = await UserLock(redis).acquire("U1", ttl_ms=10_000, wait_s=0)
    assert held is not None
    try:
        async with held:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert held._renew_task is None
    assert await redis.get("lock:user:U1") is None

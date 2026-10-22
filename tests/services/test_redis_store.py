"""tests/services/test_redis_store.py — RedisReplyTokens (FakeRedis)"""

from __future__ import annotations

import pytest

from app.pipeline.ports import ReplyTokens
from app.services.redis_store import RedisReplyTokens
from tests.infra_fakes import FakeRedis


async def test_put_sets_key_with_ttl_and_take_is_single_use() -> None:
    redis = FakeRedis()
    tokens = RedisReplyTokens(redis, ttl_s=50)
    assert isinstance(tokens, ReplyTokens)
    await tokens.put(7, "tok-abc")
    assert ("set", ("replytoken:7", 50, None, False)) in redis.calls
    assert await tokens.take(7) == "tok-abc"
    assert await tokens.take(7) is None
    assert redis.count("getdel") == 2


async def test_take_missing_returns_none() -> None:
    assert await RedisReplyTokens(FakeRedis(), ttl_s=50).take(1) is None


def test_ttl_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RedisReplyTokens(FakeRedis(), ttl_s=0)

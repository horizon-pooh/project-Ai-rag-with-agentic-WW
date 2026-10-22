"""tests/worker/test_settings_deps.py — WorkerSettings ของ arq และ build_deps / close_resources (ไม่ต่อของจริง)"""

from __future__ import annotations

from typing import Any

import pytest

from app.pipeline import orchestrator
from app.pipeline.ports import SystemClock
from app.services.ai_client import HttpAiClient, StubAiClient
from app.services.redis_store import RedisReplyTokens
from app.worker import deps as deps_mod
from app.worker import settings as ws
from app.worker.locks import UserLock
from app.worker.tasks import process_event
from tests.fakes import make_settings
from tests.infra_fakes import FakeRedis


class DummyPool:
    def __init__(self) -> None:
        self.closed = False


@pytest.fixture
def fake_infra(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {"pools": [], "redis": [], "closed_pools": []}

    async def open_pool(url: Any, **kw: Any) -> DummyPool:
        pool = DummyPool()
        made["pools"].append(pool)
        return pool

    async def close_pool(pool: DummyPool) -> None:
        made["closed_pools"].append(pool)

    async def create_pool(settings: Any) -> FakeRedis:
        if made.get("redis_fail"):
            raise ConnectionError("redis down")
        r = FakeRedis()
        made["redis"].append(r)
        return r

    monkeypatch.setattr(deps_mod, "open_pool", open_pool)
    monkeypatch.setattr(deps_mod, "close_pool", close_pool)
    monkeypatch.setattr(deps_mod, "create_pool", create_pool)
    return made


def test_worker_settings_values() -> None:
    cls = ws.make_worker_settings(make_settings(ai_concurrency=1, turn_deadline_s=50))
    assert cls.functions == [process_event]
    assert cls.max_jobs == 4
    assert cls.job_timeout == 110
    assert cls.keep_result == 0
    assert {c.name for c in cls.cron_jobs} == {
        "requeue_pending",
        "release_stuck",
        "handoff_timeout",
        "retention",
    }
    assert cls.redis_settings.host == "localhost"
    assert ws.make_worker_settings(make_settings(ai_concurrency=4)).max_jobs == 8


async def test_worker_startup_and_shutdown(
    fake_infra: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = object()
    monkeypatch.setattr(orchestrator, "load_pipeline_config", lambda s: cfg, raising=False)
    monkeypatch.setattr(ws, "init_observability", lambda s: None)
    cls = ws.make_worker_settings(make_settings())
    ctx: dict[str, Any] = {}
    await cls.on_startup(ctx)
    assert ctx["pipeline_cfg"] is cfg
    assert isinstance(ctx["user_lock"], UserLock)
    assert ctx["raw_retention"] is ctx["resources"].repos.raw_events
    await cls.on_shutdown(ctx)
    assert fake_infra["closed_pools"] == fake_infra["pools"]
    assert fake_infra["redis"][0].count("aclose") == 1


async def test_build_deps_wires_ports(fake_infra: dict[str, Any]) -> None:
    deps, res = await deps_mod.build_deps(make_settings())
    try:
        assert isinstance(deps.clock, SystemClock)
        assert isinstance(deps.reply_tokens, RedisReplyTokens)
        assert isinstance(deps.ai, StubAiClient)
        assert deps.raw_events is res.repos.raw_events
        assert res.redis is fake_infra["redis"][0]
    finally:
        await deps_mod.close_resources(res)
    assert res.line_http.is_closed and res.ai_http.is_closed

    deps2, res2 = await deps_mod.build_deps(make_settings(ai_api_url="https://ai.example.test"))
    try:
        assert isinstance(deps2.ai, HttpAiClient)
    finally:
        await deps_mod.close_resources(res2)


async def test_build_deps_closes_pool_when_redis_fails(fake_infra: dict[str, Any]) -> None:
    fake_infra["redis_fail"] = True
    with pytest.raises(ConnectionError):
        await deps_mod.build_deps(make_settings())
    assert fake_infra["closed_pools"] == fake_infra["pools"]


async def test_check_ready(monkeypatch: pytest.MonkeyPatch, fake_infra: dict[str, Any]) -> None:
    _, res = await deps_mod.build_deps(make_settings())

    async def ping_ok(pool: Any) -> bool:
        return True

    monkeypatch.setattr(deps_mod, "ping_db", ping_ok)
    assert await deps_mod.check_ready(res) == {"postgres": True, "redis": True}
    res.redis.ping_ok = False
    assert await deps_mod.check_ready(res) == {"postgres": True, "redis": False}
    await deps_mod.close_resources(res)

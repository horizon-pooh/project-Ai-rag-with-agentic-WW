"""tests/worker/test_deps.py — build_deps / close_resources / check_ready (ไม่ต่อของจริง)"""

from __future__ import annotations

from typing import Any

import pytest

from app.pipeline.ports import ReplyTokens, SystemClock
from app.repositories import PgReplyTokens
from app.services.ai_client import HttpAiClient, StubAiClient
from app.worker import deps as deps_mod
from tests.fakes import make_settings


class DummyPool:
    def __init__(self) -> None:
        self.closed = False


@pytest.fixture
def fake_infra(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {"pools": [], "closed_pools": []}

    async def open_pool(url: Any, **kw: Any) -> DummyPool:
        pool = DummyPool()
        made["pools"].append(pool)
        return pool

    async def close_pool(pool: DummyPool) -> None:
        made["closed_pools"].append(pool)

    monkeypatch.setattr(deps_mod, "open_pool", open_pool)
    monkeypatch.setattr(deps_mod, "close_pool", close_pool)
    return made


async def test_build_deps_wires_ports(fake_infra: dict[str, Any]) -> None:
    deps, res = await deps_mod.build_deps(make_settings(reply_token_ttl_s=42))
    try:
        assert isinstance(deps.clock, SystemClock)
        # replyToken อยู่ Postgres (ไม่มี Redis แล้ว) ตัวเดียวกับที่ cron retention ใช้
        assert isinstance(deps.reply_tokens, PgReplyTokens)
        assert isinstance(deps.reply_tokens, ReplyTokens)
        assert deps.reply_tokens is res.reply_tokens
        assert res.reply_tokens._ttl_s == 42
        assert isinstance(deps.ai, StubAiClient)
        assert deps.raw_events is res.repos.raw_events
        assert res.pool is fake_infra["pools"][0]
        assert not hasattr(res, "redis")
    finally:
        await deps_mod.close_resources(res)
    assert fake_infra["closed_pools"] == fake_infra["pools"]
    assert res.line_http.is_closed and res.ai_http.is_closed

    deps2, res2 = await deps_mod.build_deps(make_settings(ai_api_url="https://ai.example.test"))
    try:
        assert isinstance(deps2.ai, HttpAiClient)
    finally:
        await deps_mod.close_resources(res2)


async def test_build_deps_closes_pool_when_later_step_fails(
    fake_infra: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(pool: Any) -> Any:
        raise RuntimeError("wiring failed")

    monkeypatch.setattr(deps_mod, "make_repos", broken)
    with pytest.raises(RuntimeError):
        await deps_mod.build_deps(make_settings())
    assert fake_infra["closed_pools"] == fake_infra["pools"]


async def test_build_deps_fails_fast_when_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_pool(url: Any, **kw: Any) -> Any:
        raise ConnectionError("db down")

    monkeypatch.setattr(deps_mod, "open_pool", open_pool)
    with pytest.raises(ConnectionError):
        await deps_mod.build_deps(make_settings())


async def test_check_ready_postgres_only(
    monkeypatch: pytest.MonkeyPatch, fake_infra: dict[str, Any]
) -> None:
    _, res = await deps_mod.build_deps(make_settings())
    state = {"ok": True}

    async def ping(pool: Any) -> bool:
        if not state["ok"]:
            raise ConnectionError("postgresql://user:pw@db/x")
        return True

    monkeypatch.setattr(deps_mod, "ping_db", ping)
    assert await deps_mod.check_ready(res) == {"postgres": True}
    state["ok"] = False
    assert await deps_mod.check_ready(res) == {"postgres": False}
    await deps_mod.close_resources(res)

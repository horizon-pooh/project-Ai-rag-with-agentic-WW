"""tests/api/test_health.py — /health (liveness) และ /health/ready (readiness) + lifespan ของ create_app"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main
from app.pipeline import orchestrator
from tests.api._app import make_test_app
from tests.fakes import make_deps, make_settings


def test_liveness_needs_nothing() -> None:
    app, _ = make_test_app()
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("checks", "code"),
    [
        ({"postgres": True, "redis": True}, 200),
        ({"postgres": True, "redis": False}, 503),
        ({"postgres": False, "redis": True}, 503),
    ],
)
def test_ready(checks: dict[str, bool], code: int) -> None:
    app, deps = make_test_app()

    async def check() -> dict[str, bool]:
        return checks

    app.state.ready_check = check
    resp = TestClient(app).get("/health/ready")
    assert resp.status_code == code
    assert resp.json()["checks"] == checks
    # ไม่เปิดเผย secret / config
    for secret in ("test-channel-secret", "test-access-token", "postgresql://", "redis://"):
        assert secret not in resp.text


def test_ready_check_raising_or_missing_is_503() -> None:
    app, _ = make_test_app()
    assert TestClient(app).get("/health/ready").status_code == 503

    async def boom() -> dict[str, bool]:
        raise RuntimeError("postgresql://user:pw@db/x")

    app.state.ready_check = boom
    resp = TestClient(app).get("/health/ready")
    assert resp.status_code == 503
    assert "pw" not in resp.text


def test_no_dev_routes() -> None:
    app, _ = make_test_app()
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_lifespan_builds_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings()
    deps = make_deps(settings=settings)
    calls: list[str] = []

    class Res:
        redis = object()

    async def build(s: Any) -> tuple[Any, Any]:
        calls.append("build")
        return deps, Res()

    async def close(res: Any) -> None:
        calls.append("close")

    async def ready(res: Any) -> dict[str, bool]:
        return {"postgres": True, "redis": True}

    cfg = object()
    monkeypatch.setattr(main, "build_deps", build)
    monkeypatch.setattr(main, "close_resources", close)
    monkeypatch.setattr(main, "check_ready", ready)
    monkeypatch.setattr(main, "init_observability", lambda s: calls.append("obs"))
    monkeypatch.setattr(orchestrator, "load_pipeline_config", lambda s: cfg, raising=False)

    app = main.create_app(settings)
    with TestClient(app) as client:
        assert app.state.deps is deps
        assert app.state.pipeline_cfg is cfg
        assert app.state.arq is Res.redis
        assert client.get("/health/ready").status_code == 200
    assert calls == ["obs", "build", "close"]

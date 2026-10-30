"""tests/api/test_assets.py — รูปแทนบนการ์ดสินค้า (app/api/assets.py)"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.flex_builder import PLACEHOLDER_IMAGE_PATH
from tests.api._app import make_test_app

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_placeholder_image_served_as_png() -> None:
    app, _ = make_test_app()
    resp = TestClient(app).get(PLACEHOLDER_IMAGE_PATH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(_PNG_SIGNATURE)
    assert "max-age" in resp.headers["cache-control"]


def test_placeholder_image_head() -> None:
    app, _ = make_test_app()
    assert TestClient(app).head(PLACEHOLDER_IMAGE_PATH).status_code == 200

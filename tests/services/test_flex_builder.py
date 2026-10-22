"""tests/services/test_flex_builder.py — การ์ดสินค้า (app/services/flex_builder.py)"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.schemas import (
    LINE_MAX_ALT_TEXT_CHARS,
    MAX_CAROUSEL_BUBBLES,
    Product,
    parse_postback_data,
)
from app.services.flex_builder import (
    LABEL_DETAIL,
    LABEL_OPEN_PAGE,
    format_price,
    product_bubble,
    product_carousel,
    product_detail_message,
)


def _p(sku: str = "A1", **kw: object) -> Product:
    values: dict[str, object] = {
        "sku": sku,
        "name": f"ตู้เย็น {sku}",
        "url": f"https://shop.example/{sku}",
    }
    values.update(kw)
    return Product(**values)  # type: ignore[arg-type]


def _texts(bubble: dict) -> list[str]:
    return [c["text"] for c in bubble["body"]["contents"] if c["type"] == "text"]


def test_format_price() -> None:
    assert format_price(Decimal("12990")) == "฿12,990"
    assert format_price(Decimal("1290.40")) == "฿1,290"
    assert format_price(Decimal("0")) == "฿0"


def test_bubble_full() -> None:
    b = product_bubble(_p(price=Decimal("12990"), image_url="https://img.example/a1.jpg"))
    assert b["type"] == "bubble"
    assert b["hero"] == {
        "type": "image",
        "url": "https://img.example/a1.jpg",
        "size": "full",
        "aspectRatio": "1:1",
        "aspectMode": "cover",
    }
    assert _texts(b) == ["ตู้เย็น A1", "฿12,990"]
    detail, open_page = (c["action"] for c in b["footer"]["contents"])
    assert detail["type"] == "postback" and detail["label"] == LABEL_DETAIL
    assert parse_postback_data(detail["data"]) == {"action": "detail", "sku": "A1"}
    assert open_page == {"type": "uri", "label": LABEL_OPEN_PAGE, "uri": "https://shop.example/A1"}


def test_bubble_without_image_and_price() -> None:
    b = product_bubble(_p())
    assert "hero" not in b
    assert _texts(b) == ["ตู้เย็น A1"]


def test_sku_with_special_chars_is_urlencoded_in_postback() -> None:
    b = product_bubble(_p(sku="A&B=1 x"))
    data = b["footer"]["contents"][0]["action"]["data"]
    assert parse_postback_data(data) == {"action": "detail", "sku": "A&B=1 x"}


def test_carousel_shape_and_alt_text() -> None:
    msg = product_carousel([_p("A"), _p("B")])
    assert msg["type"] == "flex"
    assert msg["contents"]["type"] == "carousel"
    assert len(msg["contents"]["contents"]) == 2
    assert "ตู้เย็น A" in msg["altText"] and "ตู้เย็น B" in msg["altText"]


def test_alt_text_is_capped() -> None:
    products = [_p(f"S{i}", name="ชื่อยาวมาก" * 30) for i in range(MAX_CAROUSEL_BUBBLES)]
    alt = product_carousel(products)["altText"]
    assert 0 < len(alt) <= LINE_MAX_ALT_TEXT_CHARS
    assert alt.endswith("…")


def test_carousel_rejects_empty_and_too_many() -> None:
    with pytest.raises(ValueError):
        product_carousel([])
    with pytest.raises(ValueError):
        product_carousel([_p(f"S{i}") for i in range(MAX_CAROUSEL_BUBBLES + 1)])


def test_detail_message() -> None:
    msg = product_detail_message(_p(price=Decimal("1500")))
    assert msg["type"] == "text"
    assert msg["text"] == "ตู้เย็น A1\nรหัสสินค้า: A1\nราคา: ฿1,500\nhttps://shop.example/A1"
    assert "ราคา" not in product_detail_message(_p())["text"]

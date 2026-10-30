"""tests/services/test_flex_builder.py — การ์ดสินค้า (app/services/flex_builder.py)"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.pipeline.schemas import (
    LINE_MAX_ALT_TEXT_CHARS,
    MAX_CAROUSEL_BUBBLES,
    Product,
)
from app.services.flex_builder import (
    COLOR_ACCENT,
    COLOR_PRICE,
    COLOR_PRIMARY,
    DETAILS_MAX_CHARS,
    LABEL_OPEN_PAGE,
    format_price,
    placeholder_image_url,
    product_bubble,
    product_carousel,
    product_detail_message,
)

PLACEHOLDER = "https://bot.example/static/no-image.png"


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
    b = product_bubble(
        _p(
            price=Decimal("12990"),
            image_url="https://img.example/a1.jpg",
            brand="HISENSE",
            details="ระบบ Inverter, รับประกัน 10 ปี",
        )
    )
    open_page = {"type": "uri", "label": LABEL_OPEN_PAGE, "uri": "https://shop.example/A1"}
    assert b["type"] == "bubble"
    assert b["hero"] == {
        "type": "image",
        "url": "https://img.example/a1.jpg",
        "size": "full",
        "aspectRatio": "1:1",
        "aspectMode": "cover",
        "action": open_page,
    }
    assert _texts(b) == [
        "HISENSE",
        "ตู้เย็น A1",
        "ระบบ Inverter, รับประกัน 10 ปี",
        "฿12,990",
        "รหัสสินค้า A1",
    ]
    brand, name, _, price, _ = b["body"]["contents"]
    assert brand["color"] == COLOR_ACCENT
    assert name["color"] == COLOR_PRIMARY
    assert price["color"] == COLOR_PRICE
    assert b["body"]["action"] == open_page
    # ปุ่มเดียว: เปิดหน้าสินค้า (ไม่มีปุ่ม "ดูรายละเอียด" / postback แล้ว)
    (button,) = b["footer"]["contents"]
    assert button["action"] == open_page
    assert button["color"] == COLOR_PRIMARY


def test_bubble_without_optional_fields() -> None:
    b = product_bubble(_p())
    assert "hero" not in b
    assert _texts(b) == ["ตู้เย็น A1", "รหัสสินค้า A1"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_brand_and_details_not_shown(blank: str) -> None:
    # flex ห้าม text ว่าง
    assert _texts(product_bubble(_p(brand=blank, details=blank))) == ["ตู้เย็น A1", "รหัสสินค้า A1"]


def test_no_image_uses_placeholder() -> None:
    b = product_bubble(_p(), placeholder_image_url=PLACEHOLDER)
    assert b["hero"]["url"] == PLACEHOLDER


def test_own_image_wins_over_placeholder() -> None:
    b = product_bubble(_p(image_url="https://img.example/a1.jpg"), placeholder_image_url=PLACEHOLDER)
    assert b["hero"]["url"] == "https://img.example/a1.jpg"


def test_long_details_clipped() -> None:
    details = product_bubble(_p(details="ก" * 500))["body"]["contents"][1]["text"]
    assert len(details) == DETAILS_MAX_CHARS
    assert details.endswith("…")


def test_carousel_passes_placeholder_to_every_bubble() -> None:
    msg = product_carousel(
        [_p("A"), _p("B", image_url="https://img.example/b.jpg")], placeholder_image_url=PLACEHOLDER
    )
    urls = [b["hero"]["url"] for b in msg["contents"]["contents"]]
    assert urls == [PLACEHOLDER, "https://img.example/b.jpg"]


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://bot.example", PLACEHOLDER),
        ("https://bot.example/", PLACEHOLDER),
        (None, None),
        ("", None),
    ],
)
def test_placeholder_image_url(base: str | None, expected: str | None) -> None:
    assert placeholder_image_url(base) == expected


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

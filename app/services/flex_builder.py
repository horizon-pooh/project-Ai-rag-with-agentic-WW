"""app/services/flex_builder.py

สร้างข้อความ LINE จากข้อมูลดิบ (โค้ดสร้าง ไม่ใช่ AI)

รายละเอียด:
 - product carousel: การ์ดสินค้าไม่เกิน 10 ใบ ราคา/ลิงก์/SKU มาจาก products ของ API เท่านั้น
 - ปุ่ม 'ดูรายละเอียด' เป็น postback ที่มี SKU
 - quick reply: คุยกับพนักงาน, เริ่มใหม่ ฯลฯ
 - text templates: กลับมาคุยกับ AI แล้ว, กำลังคุยกับ AI อยู่, หมดเวลาคุยกับพนักงานแล้ว, ข้อมูลก่อนหน้าหมดอายุ, ขอโทษระบบขัดข้อง, กำลังค้นหา
 - เคารพขีดจำกัดของ LINE (validate ตรวจซ้ำ)

แนวคิดจาก: src/line/messages/productCarousel.js, quickReply.js, textMessages.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (product_carousel, product_bubble, product_detail_message — ฟังก์ชันล้วน ไม่มี I/O)
       quick reply และ text templates ย้ายไปอยู่ที่ app/pipeline/replies.py (ค่าคงที่ที่เดียว ตาม docs/PLAN.md)
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.pipeline.schemas import (
    LINE_MAX_ALT_TEXT_CHARS,
    LINE_MAX_TEXT_CHARS,
    MAX_CAROUSEL_BUBBLES,
    JsonDict,
    PostbackAction,
    Product,
    postback_data,
)

# ป้ายปุ่มบนการ์ด
LABEL_DETAIL = "ดูรายละเอียด"
LABEL_OPEN_PAGE = "เปิดหน้าสินค้า"

ALT_TEXT_PREFIX = "สินค้าแนะนำ: "
_ELLIPSIS = "…"


def format_price(price: Decimal) -> str:
    """ราคาแบบ ฿1,290 (ไม่มีทศนิยม)"""
    return f"฿{price:,.0f}"


def detail_postback_data(product: Product) -> str:
    """postback data ของปุ่ม "ดูรายละเอียด" -> action=detail&sku=<SKU>"""
    return postback_data(PostbackAction.DETAIL, sku=product.sku)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def product_bubble(product: Product) -> JsonDict:
    """bubble หนึ่งใบ: รูป (ถ้ามี image_url), ชื่อ, ราคา (ถ้ามี), ปุ่มดูรายละเอียด + เปิดหน้าสินค้า"""
    body: list[JsonDict] = [
        {
            "type": "text",
            "text": product.name,
            "weight": "bold",
            "size": "md",
            "wrap": True,
            "maxLines": 3,
        }
    ]
    if product.price is not None:
        body.append(
            {"type": "text", "text": format_price(product.price), "size": "lg", "color": "#E53935"}
        )

    bubble: JsonDict = {"type": "bubble"}
    if product.image_url:
        bubble["hero"] = {
            "type": "image",
            "url": product.image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
        }
    bubble["body"] = {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}
    bubble["footer"] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
            {
                "type": "button",
                "style": "primary",
                "height": "sm",
                "action": {
                    "type": "postback",
                    "label": LABEL_DETAIL,
                    "data": detail_postback_data(product),
                    "displayText": LABEL_DETAIL,
                },
            },
            {
                "type": "button",
                "style": "secondary",
                "height": "sm",
                "action": {"type": "uri", "label": LABEL_OPEN_PAGE, "uri": product.url},
            },
        ],
    }
    return bubble


def carousel_alt_text(products: Sequence[Product]) -> str:
    """altText (ข้อความแจ้งเตือน/อุปกรณ์ที่แสดง flex ไม่ได้) ไม่เกิน LINE_MAX_ALT_TEXT_CHARS"""
    return _clip(ALT_TEXT_PREFIX + ", ".join(p.name for p in products), LINE_MAX_ALT_TEXT_CHARS)


def product_carousel(products: Sequence[Product]) -> JsonDict:
    """flex message แบบ carousel ของสินค้า (1..MAX_CAROUSEL_BUBBLES ใบ ไม่งั้น ValueError)

    ไม่ตรวจความถูกต้องของ url/ราคา — steps/validate.py ตรวจก่อนเรียก
    """
    if not products:
        raise ValueError("carousel ต้องมีสินค้าอย่างน้อย 1 รายการ")
    if len(products) > MAX_CAROUSEL_BUBBLES:
        raise ValueError(f"carousel เกิน {MAX_CAROUSEL_BUBBLES} ใบ")
    return {
        "type": "flex",
        "altText": carousel_alt_text(products),
        "contents": {"type": "carousel", "contents": [product_bubble(p) for p in products]},
    }


def product_detail_message(product: Product) -> JsonDict:
    """text message ตอบปุ่ม "ดูรายละเอียด" (ข้อมูลจาก counters.last_products เท่านั้น ไม่ถาม AI)"""
    lines = [product.name, f"รหัสสินค้า: {product.sku}"]
    if product.price is not None:
        lines.append(f"ราคา: {format_price(product.price)}")
    lines.append(product.url)
    return {"type": "text", "text": _clip("\n".join(lines), LINE_MAX_TEXT_CHARS)}

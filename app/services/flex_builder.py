"""app/services/flex_builder.py

สร้างข้อความ LINE จากข้อมูลดิบ (โค้ดสร้าง ไม่ใช่ AI)

รายละเอียด:
 - product carousel: การ์ดสินค้าไม่เกิน 10 ใบ ราคา/ลิงก์/SKU มาจาก products ของ API เท่านั้น
 - รายละเอียดสินค้า (แบรนด์, Details, รหัสสินค้า) อยู่บนการ์ดเลย ไม่มีปุ่ม 'ดูรายละเอียด' (ตัดออก 2026-10-26)
   แตะรูป/เนื้อการ์ด/ปุ่ม 'เปิดหน้าสินค้า' -> เปิดหน้าสินค้า
 - สินค้าที่ไม่มีรูป -> รูปแทน (placeholder_image_url) ถ้ามี ไม่งั้นไม่มีรูป
 - สีตามเว็บ/โลโก้ HomePro (ดู COLOR_*)
 - quick reply: คุยกับพนักงาน, เริ่มใหม่ ฯลฯ
 - text templates: กลับมาคุยกับ AI แล้ว, กำลังคุยกับ AI อยู่, หมดเวลาคุยกับพนักงานแล้ว, ข้อมูลก่อนหน้าหมดอายุ, ขอโทษระบบขัดข้อง, กำลังค้นหา
 - เคารพขีดจำกัดของ LINE (validate ตรวจซ้ำ)

แนวคิดจาก: src/line/messages/productCarousel.js, quickReply.js, textMessages.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (product_carousel, product_bubble, product_detail_message — ฟังก์ชันล้วน ไม่มี I/O)
       quick reply และ text templates ย้ายไปอยู่ที่ app/pipeline/replies.py (ค่าคงที่ที่เดียว ตาม docs/PLAN.md)
       product_detail_message ยังใช้ตอบปุ่ม 'ดูรายละเอียด' บนการ์ดเก่าที่ส่งไปก่อนตัดปุ่ม (postback action=detail)
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.pipeline.schemas import (
    LINE_MAX_ALT_TEXT_CHARS,
    LINE_MAX_TEXT_CHARS,
    MAX_CAROUSEL_BUBBLES,
    JsonDict,
    Product,
)

# ป้ายปุ่มบนการ์ด
LABEL_OPEN_PAGE = "เปิดหน้าสินค้า"
LABEL_SKU = "รหัสสินค้า"

ALT_TEXT_PREFIX = "สินค้าแนะนำ: "
_ELLIPSIS = "…"

# สีจากเว็บ homepro.co.th (style-common.css) และโลโก้ Logo-2026.svg — เช็ค 2026-10-26
COLOR_PRIMARY = "#27509A"  # น้ำเงินของโลโก้: ชื่อสินค้า, ปุ่ม
COLOR_ACCENT = "#F79420"  # ส้มของเว็บ: แบรนด์
COLOR_PRICE = "#DA3731"  # แดงที่เว็บใช้กับราคา
COLOR_TEXT = "#525255"  # ตัวหนังสือหลักของเว็บ
COLOR_MUTED = "#99999C"  # รหัสสินค้า

# ความยาวบนการ์ด (ข้อความจาก AI ยาวได้ไม่จำกัด — กันการ์ดสูงเกินและ flex เกินขนาด)
BRAND_MAX_CHARS = 40
DETAILS_MAX_CHARS = 160
NAME_MAX_LINES = 3
DETAILS_MAX_LINES = 4

# รูปแทนสินค้าที่ไม่มีรูป เสิร์ฟโดย app/api/assets.py (ต้องเปิดผ่าน Caddy ด้วย — LINE โหลดรูปเอง)
PLACEHOLDER_IMAGE_PATH = "/static/no-image.png"


def format_price(price: Decimal) -> str:
    """ราคาแบบ ฿1,290 (ไม่มีทศนิยม)"""
    return f"฿{price:,.0f}"


def placeholder_image_url(public_base_url: str | None) -> str | None:
    """URL เต็มของรูปแทน; ไม่ได้ตั้ง PUBLIC_BASE_URL -> None (การ์ดที่ไม่มีรูปก็ไม่มีรูป)"""
    if not public_base_url:
        return None
    return public_base_url.rstrip("/") + PLACEHOLDER_IMAGE_PATH


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def _open_page(product: Product) -> JsonDict:
    return {"type": "uri", "label": LABEL_OPEN_PAGE, "uri": product.url}


def product_bubble(product: Product, *, placeholder_image_url: str | None = None) -> JsonDict:
    """bubble หนึ่งใบ: รูป (หรือรูปแทน), แบรนด์, ชื่อ, รายละเอียด, ราคา, รหัสสินค้า, ปุ่มเปิดหน้าสินค้า

    แบรนด์/รายละเอียด/ราคา ไม่มีค่า -> ไม่แสดงบรรทัดนั้น (flex ห้าม text ว่าง)
    """
    body: list[JsonDict] = []
    brand = (product.brand or "").strip()
    if brand:
        body.append(
            {
                "type": "text",
                "text": _clip(brand, BRAND_MAX_CHARS),
                "size": "xs",
                "weight": "bold",
                "color": COLOR_ACCENT,
            }
        )
    body.append(
        {
            "type": "text",
            "text": product.name,
            "weight": "bold",
            "size": "sm",
            "color": COLOR_PRIMARY,
            "wrap": True,
            "maxLines": NAME_MAX_LINES,
        }
    )
    details = (product.details or "").strip()
    if details:
        body.append(
            {
                "type": "text",
                "text": _clip(details, DETAILS_MAX_CHARS),
                "size": "xs",
                "color": COLOR_TEXT,
                "wrap": True,
                "maxLines": DETAILS_MAX_LINES,
            }
        )
    if product.price is not None:
        body.append(
            {
                "type": "text",
                "text": format_price(product.price),
                "size": "lg",
                "weight": "bold",
                "color": COLOR_PRICE,
                "margin": "md",
            }
        )
    body.append(
        {
            "type": "text",
            "text": f"{LABEL_SKU} {product.sku}",
            "size": "xxs",
            "color": COLOR_MUTED,
            "wrap": True,
        }
    )

    bubble: JsonDict = {"type": "bubble"}
    image_url = product.image_url or placeholder_image_url
    if image_url:
        bubble["hero"] = {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": _open_page(product),
        }
    bubble["body"] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": body,
        "action": _open_page(product),
    }
    bubble["footer"] = {
        "type": "box",
        "layout": "vertical",
        "contents": [
            {
                "type": "button",
                "style": "primary",
                "color": COLOR_PRIMARY,
                "height": "sm",
                "action": _open_page(product),
            }
        ],
    }
    return bubble


def carousel_alt_text(products: Sequence[Product]) -> str:
    """altText (ข้อความแจ้งเตือน/อุปกรณ์ที่แสดง flex ไม่ได้) ไม่เกิน LINE_MAX_ALT_TEXT_CHARS"""
    return _clip(ALT_TEXT_PREFIX + ", ".join(p.name for p in products), LINE_MAX_ALT_TEXT_CHARS)


def product_carousel(
    products: Sequence[Product], *, placeholder_image_url: str | None = None
) -> JsonDict:
    """flex message แบบ carousel ของสินค้า (1..MAX_CAROUSEL_BUBBLES ใบ ไม่งั้น ValueError)

    ไม่ตรวจความถูกต้องของ url/ราคา — steps/validate.py ตรวจก่อนเรียก
    """
    if not products:
        raise ValueError("carousel ต้องมีสินค้าอย่างน้อย 1 รายการ")
    if len(products) > MAX_CAROUSEL_BUBBLES:
        raise ValueError(f"carousel เกิน {MAX_CAROUSEL_BUBBLES} ใบ")
    bubbles = [product_bubble(p, placeholder_image_url=placeholder_image_url) for p in products]
    return {
        "type": "flex",
        "altText": carousel_alt_text(products),
        "contents": {"type": "carousel", "contents": bubbles},
    }


def product_detail_message(product: Product) -> JsonDict:
    """text message ตอบปุ่ม "ดูรายละเอียด" ของการ์ดเก่า (ข้อมูลจาก counters.last_products เท่านั้น ไม่ถาม AI)"""
    lines = [product.name, f"{LABEL_SKU}: {product.sku}"]
    if product.price is not None:
        lines.append(f"ราคา: {format_price(product.price)}")
    lines.append(product.url)
    return {"type": "text", "text": _clip("\n".join(lines), LINE_MAX_TEXT_CHARS)}

"""app/api/assets.py

GET /static/no-image.png: รูปแทนบนการ์ดสินค้าที่ AI ไม่ส่งรูปมา (flex_builder.PLACEHOLDER_IMAGE_PATH)

รายละเอียด:
 - LINE โหลดรูปจาก URL นี้เอง (PUBLIC_BASE_URL + path) จึงต้องเปิดสาธารณะ — Caddyfile เปิดเฉพาะ path นี้
 - ไฟล์ app/static/no-image.png (600x600 PNG รูปบ้านสีจางของ HomePro วาดด้วย Pillow ไม่มีตัวหนังสือ)
 - อ่านไฟล์ครั้งเดียวแล้วเก็บในหน่วยความจำ; Cache-Control 1 วัน (LINE/อุปกรณ์ cache ตาม URL)

แนวคิดจาก: ใหม่ (2026-10-26)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

from app.services.flex_builder import PLACEHOLDER_IMAGE_PATH

PLACEHOLDER_FILE = Path(__file__).resolve().parent.parent / "static" / "no-image.png"
CACHE_SECONDS = 86_400

router = APIRouter()


@cache
def _placeholder_png() -> bytes:
    return PLACEHOLDER_FILE.read_bytes()


@router.api_route(PLACEHOLDER_IMAGE_PATH, methods=["GET", "HEAD"])
async def placeholder_image() -> Response:
    return Response(
        content=_placeholder_png(),
        media_type="image/png",
        headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
    )

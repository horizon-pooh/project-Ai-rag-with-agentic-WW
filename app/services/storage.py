"""app/services/storage.py

เก็บและลบไฟล์รูปใน MinIO

รายละเอียด:
 - put(รูปจาก LINE) คืน key; รูปไม่เก็บใน state เก็บแค่ id
 - ย่อรูปก่อนส่ง AI (ถ้า API รับรูป) ในเธรดแยก ไม่บล็อก event loop
 - ลบตาม retention และคำขอลบตาม PDPA (ต้องลบแยกจาก DB)
 - bucket จาก MINIO_BUCKET

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว — MinioStorage (put_image/delete ตาม ports.Storage; key ผู้เรียกเป็นคนตั้ง) + resize_image;
       retention job ยังไม่ทำรอบนี้ (docs/PLAN.md)
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import structlog
from minio import Minio
from minio.error import S3Error
from PIL import Image, ImageOps

from app.config import Settings

log = structlog.get_logger(__name__)

# S3 error code ที่ถือว่าไม่ผิด
_BUCKET_EXISTS_CODES = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}
_MISSING_OBJECT_CODES = {"NoSuchKey", "NoSuchObject"}

JPEG_CONTENT_TYPE = "image/jpeg"


def _secret(value: Any) -> str | None:
    return value.get_secret_value() if value is not None else None


class MinioStorage:
    """MinIO ผ่าน minio SDK (sync) โยนไปรันใน asyncio.to_thread ไม่บล็อก event loop

    - สร้าง bucket ครั้งแรกที่ใช้ (lazy) ไม่ใช่ตอนเริ่ม — MinIO ยังไม่พร้อมก็เริ่มแอปได้
    - key ห้ามมีข้อมูลส่วนบุคคล (ผู้เรียกตั้ง เช่น images/<conversation_id>/<line_message_id>)
    - client ส่งเข้ามาได้ (เทสต์ใช้ mock)
    """

    def __init__(self, settings: Settings, client: Minio | None = None) -> None:
        self._bucket = settings.minio_bucket
        self._client = client if client is not None else Minio(
            settings.minio_endpoint,
            access_key=_secret(settings.minio_access_key),
            secret_key=_secret(settings.minio_secret_key),
            secure=settings.minio_secure,
        )
        self._bucket_ready = False
        self._bucket_lock = asyncio.Lock()

    async def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        async with self._bucket_lock:
            if self._bucket_ready:
                return
            await asyncio.to_thread(self._ensure_bucket_sync)
            self._bucket_ready = True

    def _ensure_bucket_sync(self) -> None:
        if self._client.bucket_exists(self._bucket):
            return
        try:
            self._client.make_bucket(self._bucket)
            log.info("minio_bucket_created", bucket=self._bucket)
        except S3Error as e:
            # อีก process สร้างไปก่อน -> ไม่เป็นไร
            if e.code not in _BUCKET_EXISTS_CODES:
                raise

    async def put_image(self, key: str, data: bytes, content_type: str) -> None:
        if not key:
            raise ValueError("storage key ว่าง")
        await self._ensure_bucket()
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
        )

    async def delete(self, key: str) -> None:
        if not key:
            raise ValueError("storage key ว่าง")
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, key)
        except S3Error as e:
            # ไม่มีไฟล์/ไม่มี bucket = ลบแล้ว
            if e.code not in _MISSING_OBJECT_CODES and e.code != "NoSuchBucket":
                raise


def _resize_sync(data: bytes, max_px: int, quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        # หมุนตาม EXIF ก่อน (ไม่งั้นทิ้ง EXIF แล้วรูปตะแคง)
        oriented = ImageOps.exif_transpose(img)
        if oriented.mode != "RGB":
            if "A" in oriented.getbands() or oriented.mode == "P":
                rgba = oriented.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                oriented = background
            else:
                oriented = oriented.convert("RGB")
        oriented.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        # ไม่ส่ง exif= / icc_profile= -> metadata (GPS, รุ่นกล้อง ฯลฯ ที่อาจเป็นข้อมูลส่วนบุคคล) หายหมด
        oriented.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


async def resize_image(data: bytes, max_px: int = 1024, quality: int = 85) -> tuple[bytes, str]:
    """ย่อรูปให้ด้านยาวไม่เกิน max_px (ไม่ขยาย) เป็น JPEG ตัด EXIF ทิ้ง ในเธรดแยก -> (bytes, "image/jpeg")

    อ่านรูปไม่ออก -> ValueError
    """
    if max_px < 1:
        raise ValueError("max_px ต้อง >= 1")
    if not 1 <= quality <= 95:
        raise ValueError("quality ต้องอยู่ใน 1-95")
    try:
        resized = await asyncio.to_thread(_resize_sync, data, max_px, quality)
    except (OSError, Image.DecompressionBombError) as e:
        # UnidentifiedImageError เป็น OSError
        raise ValueError(f"อ่านรูปไม่ออก: {type(e).__name__}") from e
    return resized, JPEG_CONTENT_TYPE


__all__ = ["JPEG_CONTENT_TYPE", "MinioStorage", "resize_image"]

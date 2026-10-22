"""tests/services/test_storage.py

เคสทดสอบของ app/services/storage.py

รายละเอียด:
 - resize_image: ด้านยาวไม่เกิน max_px, ไม่ขยายรูปเล็ก, ออกเป็น JPEG, EXIF หาย (PII), หมุนตาม EXIF ก่อน, รูปโปร่งใสได้
 - resize_image: อ่านไม่ออก -> ValueError
 - MinioStorage.put_image: สร้าง bucket ครั้งแรกครั้งเดียว, put_object ด้วย bucket/key/ขนาด/content_type (mock client)
 - MinioStorage.delete: ไม่มีไฟล์ไม่เป็นไร, error อื่นยกต่อ

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import asyncio
import io
from unittest.mock import MagicMock

import pytest
from minio.error import S3Error
from PIL import Image

from app.pipeline.ports import Storage
from app.services import storage as storage_mod
from app.services.storage import JPEG_CONTENT_TYPE, MinioStorage, resize_image
from tests.fakes import make_settings
from tests.services._helpers import RecordingLogger

EXIF_ORIENTATION = 0x0112
EXIF_MAKE = 0x010F


@pytest.fixture(autouse=True)
def logger(monkeypatch) -> RecordingLogger:
    rec = RecordingLogger()
    monkeypatch.setattr(storage_mod, "log", rec)
    return rec


def make_image(size=(3000, 2000), mode="RGB", fmt="JPEG", exif: dict[int, object] | None = None) -> bytes:
    color = (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)
    img = Image.new(mode, size, color[: len(mode)] if mode in ("RGB", "RGBA") else 128)
    out = io.BytesIO()
    kwargs = {}
    if exif:
        e = Image.Exif()
        for k, v in exif.items():
            e[k] = v
        kwargs["exif"] = e
    img.save(out, format=fmt, **kwargs)
    return out.getvalue()


def s3_error(code: str) -> S3Error:
    return S3Error(MagicMock(), code, "msg", "/r", "req", "host")


# --- resize_image ---


async def test_resize_limits_longest_side_and_outputs_jpeg():
    data, ctype = await resize_image(make_image((3000, 2000)), max_px=1024)
    assert ctype == JPEG_CONTENT_TYPE == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG"
        assert max(img.size) == 1024
        assert img.size == (1024, 683)


async def test_resize_does_not_upscale_small_image():
    data, _ = await resize_image(make_image((200, 100), fmt="PNG"), max_px=1024)
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (200, 100)
        assert img.format == "JPEG"


async def test_resize_strips_exif():
    src = make_image((800, 600), exif={EXIF_MAKE: "PhoneCo", EXIF_ORIENTATION: 1})
    with Image.open(io.BytesIO(src)) as img:
        assert img.getexif().get(EXIF_MAKE) == "PhoneCo"  # มี EXIF จริงก่อนย่อ
    data, _ = await resize_image(src)
    with Image.open(io.BytesIO(data)) as img:
        assert len(img.getexif()) == 0
        assert "exif" not in img.info
    assert b"PhoneCo" not in data


async def test_resize_applies_exif_orientation_before_stripping():
    # orientation 6 = หมุน 90 องศา -> รูปกว้าง 400x200 ต้องกลายเป็นตั้ง 200x400
    src = make_image((400, 200), exif={EXIF_ORIENTATION: 6})
    data, _ = await resize_image(src)
    with Image.open(io.BytesIO(data)) as img:
        assert img.size == (200, 400)


async def test_resize_handles_transparency():
    data, _ = await resize_image(make_image((300, 300), mode="RGBA", fmt="PNG"))
    with Image.open(io.BytesIO(data)) as img:
        assert img.mode == "RGB"


async def test_resize_rejects_non_image():
    with pytest.raises(ValueError):
        await resize_image(b"not an image")


@pytest.mark.parametrize("kw", [{"max_px": 0}, {"quality": 0}, {"quality": 100}])
async def test_resize_rejects_bad_params(kw):
    with pytest.raises(ValueError):
        await resize_image(make_image((10, 10)), **kw)


# --- MinioStorage ---


def storage(client: MagicMock) -> MinioStorage:
    return MinioStorage(make_settings(minio_bucket="test-bucket"), client=client)


def test_implements_port():
    assert isinstance(storage(MagicMock()), Storage)


def test_builds_real_client_from_settings():
    s = MinioStorage(make_settings(minio_access_key="ak", minio_secret_key="sk"))
    assert s._client is not None


async def test_put_image_creates_bucket_once_and_puts_object():
    client = MagicMock()
    client.bucket_exists.return_value = False
    s = storage(client)
    await s.put_image("images/7/123", b"jpegbytes", "image/jpeg")
    await s.put_image("images/7/124", b"more", "image/jpeg")

    client.bucket_exists.assert_called_once_with("test-bucket")
    client.make_bucket.assert_called_once_with("test-bucket")
    assert client.put_object.call_count == 2
    args, kwargs = client.put_object.call_args_list[0]
    bucket, key, stream, length = args
    assert (bucket, key, length) == ("test-bucket", "images/7/123", len(b"jpegbytes"))
    assert stream.read() == b"jpegbytes"
    assert kwargs == {"content_type": "image/jpeg"}


async def test_put_image_existing_bucket_not_created():
    client = MagicMock()
    client.bucket_exists.return_value = True
    await storage(client).put_image("k", b"x", "image/jpeg")
    client.make_bucket.assert_not_called()


async def test_bucket_created_by_someone_else_is_fine():
    client = MagicMock()
    client.bucket_exists.return_value = False
    client.make_bucket.side_effect = s3_error("BucketAlreadyOwnedByYou")
    await storage(client).put_image("k", b"x", "image/jpeg")
    client.put_object.assert_called_once()


async def test_concurrent_puts_ensure_bucket_once():
    client = MagicMock()
    client.bucket_exists.return_value = False
    s = storage(client)
    await asyncio.gather(*(s.put_image(f"k{i}", b"x", "image/jpeg") for i in range(5)))
    client.make_bucket.assert_called_once()
    assert client.put_object.call_count == 5


async def test_bucket_error_retried_next_time():
    client = MagicMock()
    client.bucket_exists.side_effect = [ConnectionError("minio down"), True]
    s = storage(client)
    with pytest.raises(ConnectionError):
        await s.put_image("k", b"x", "image/jpeg")
    await s.put_image("k", b"x", "image/jpeg")
    client.put_object.assert_called_once()


async def test_empty_key_rejected():
    s = storage(MagicMock())
    with pytest.raises(ValueError):
        await s.put_image("", b"x", "image/jpeg")
    with pytest.raises(ValueError):
        await s.delete("")


async def test_delete_calls_remove_object():
    client = MagicMock()
    await storage(client).delete("images/7/123")
    client.remove_object.assert_called_once_with("test-bucket", "images/7/123")


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchBucket"])
async def test_delete_missing_is_ok(code):
    client = MagicMock()
    client.remove_object.side_effect = s3_error(code)
    await storage(client).delete("k")


async def test_delete_other_error_raises():
    client = MagicMock()
    client.remove_object.side_effect = s3_error("AccessDenied")
    with pytest.raises(S3Error):
        await storage(client).delete("k")

"""tests/privacy/test_redact.py — app/privacy/redact.py

ทุกรูปแบบมีทั้งเคสที่ต้องปิด และเคสข้อมูลสินค้าที่ต้องไม่แตะ (ประโยคไทยจริง ติดภาษาไทยไม่มีช่องว่าง)
"""

from __future__ import annotations

import pytest

from app.privacy.redact import (
    ACCOUNT,
    CARD,
    EMAIL,
    NATIONAL_ID,
    PHONE,
    TOKEN,
    redact_obj,
    redact_text,
)

# เลขที่ผ่าน checksum (สร้างขึ้นเอง ไม่ใช่ของคนจริง)
VALID_ID = "1103702071811"
VALID_ID_2 = "3101234567893"
VISA = "4111111111111111"
MASTERCARD = "5555555555554444"
LINE_TOKEN = (
    "kH3vQ9xLmN2pR7sT1uW4yZ6aB8cD0eF5gJ+iK/lO3qS7vX9zA2bC4dE6fG8hI0jK1mN3pQ5rS7tU9wY="
)
JWT = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)
REPLY_TOKEN = "b60d432864f44d079f6d8efe86cf404b"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # เบอร์มือถือ: ติดกัน, ติดภาษาไทย, ขีด, ช่องว่าง, จุด
        ("เบอร์0812345678ค่ะ", f"เบอร์{PHONE}ค่ะ"),
        ("โทร 081-234-5678 นะคะ", f"โทร {PHONE} นะคะ"),
        ("ติดต่อกลับที่ 081 234 5678 ได้เลยค่ะ", f"ติดต่อกลับที่ {PHONE} ได้เลยค่ะ"),
        ("โทร.08-1234-5678 ช่วงเย็น", f"โทร.{PHONE} ช่วงเย็น"),
        ("เบอร์ 089.876.5432 ค่ะ", f"เบอร์ {PHONE} ค่ะ"),
        ("โทรมา0612345678ได้ตลอด", f"โทรมา{PHONE}ได้ตลอด"),
        ("Tel: 0912345678", f"Tel: {PHONE}"),
        # +66 / 66
        ("เบอร์ +66 81 234 5678 ค่ะ", f"เบอร์ {PHONE} ค่ะ"),
        ("+66812345678", PHONE),
        ("เบอร์66812345678ครับ", f"เบอร์{PHONE}ครับ"),
        ("+66-2-123-4567", PHONE),
        ("+66 081 234 5678", PHONE),
        # เบอร์บ้าน
        ("เบอร์บ้าน 02-123-4567 ค่ะ", f"เบอร์บ้าน {PHONE} ค่ะ"),
        ("ร้านโทร053123456นะ", f"ร้านโทร{PHONE}นะ"),
        ("(02) 123 4567", PHONE),
        # หลายเบอร์ / ปนกับราคา
        ("0812345678/0898765432", f"{PHONE}/{PHONE}"),
        ("081-234-5678-02-123-4567", f"{PHONE}-{PHONE}"),
        ("ราคา 12990 โทร 0812345678", f"ราคา 12990 โทร {PHONE}"),
        ("ราคา 12990 0812345678", f"ราคา 12990 {PHONE}"),
        ("สั่ง 2 081 234 5678", f"สั่ง 2 {PHONE}"),
        # เลขไทย
        ("โทร ๐๘๑๒๓๔๕๖๗๘ ค่ะ", f"โทร {PHONE} ค่ะ"),
        # เบอร์ต่างประเทศ
        ("เบอร์ญี่ปุ่น +81 90 1234 5678", f"เบอร์ญี่ปุ่น {PHONE}"),
    ],
)
def test_phone(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("อีเมลsomchai.j@gmail.comค่ะ", f"อีเมล{EMAIL}ค่ะ"),
        ("ส่งใบเสนอราคาที่ somchai_99+shop@company.co.th นะคะ", f"ส่งใบเสนอราคาที่ {EMAIL} นะคะ"),
        ("เมล: Malee.S@Hotmail.com", f"เมล: {EMAIL}"),
        ("0812345678@gmail.com", EMAIL),
    ],
)
def test_email(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"เลขบัตรประชาชน{VALID_ID}ค่ะ", f"เลขบัตรประชาชน{NATIONAL_ID}ค่ะ"),
        (f"บัตร ปชช. {VALID_ID_2}", f"บัตร ปชช. {NATIONAL_ID}"),
        ("เลขบัตร 1-1037-02071-81-1 ค่ะ", f"เลขบัตร {NATIONAL_ID} ค่ะ"),
        ("เลขบัตร 1 1037 02071 81 1", f"เลขบัตร {NATIONAL_ID}"),
        # รูป x-xxxx-xxxxx-xx-x ปิดแม้ checksum ผิด (พิมพ์พลาดก็ยังเป็นข้อมูลส่วนบุคคล)
        ("3-1012-34567-89-1", NATIONAL_ID),
    ],
)
def test_national_id(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"จ่ายด้วยบัตร{VISA}ได้ไหม", f"จ่ายด้วยบัตร{CARD}ได้ไหม"),
        ("บัตร 4111 1111 1111 1111 หมดอายุ 12/28", f"บัตร {CARD} หมดอายุ 12/28"),
        ("5555-5555-5555-4444", CARD),
        (f"บัตรเครดิต{MASTERCARD}ค่ะ", f"บัตรเครดิต{CARD}ค่ะ"),
        ("Amex 3782 822463 10005", f"Amex {CARD}"),
    ],
)
def test_card(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("บัญชีกสิกร 123-4-56789-0 ค่ะ", f"บัญชีกสิกร {ACCOUNT} ค่ะ"),
        ("โอนคืนเข้าบัญชี1234567890นะคะ", f"โอนคืนเข้าบัญชี{ACCOUNT}นะคะ"),
        ("ออมสิน 020012345678", f"ออมสิน {ACCOUNT}"),
        ("บัญชี 123 4 56789 012", f"บัญชี {ACCOUNT}"),
    ],
)
def test_account(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Authorization: Bearer abcdefghijklmnop1234567890", f"Authorization: Bearer {TOKEN}"),
        (f"token ของ LINE คือ{LINE_TOKEN}ค่ะ", f"token ของ LINE คือ{TOKEN}ค่ะ"),
        (f"jwt {JWT}", f"jwt {TOKEN}"),
        (f"replyToken={REPLY_TOKEN}", f"replyToken={TOKEN}"),
        (f"reply {REPLY_TOKEN} หมดอายุ", f"reply {TOKEN} หมดอายุ"),
        ("api_key=sk_live_abc12345", f"api_key={TOKEN}"),
        ('{"channel_secret": "0123456789abcdef"}', f'{{"channel_secret": "{TOKEN}"}}'),
        ("x-line-signature: abc123def456ghi", f"x-line-signature: {TOKEN}"),
        # URL: query ที่ชื่อเป็น secret, segment ที่ดูเป็น token
        (
            "ปิดเคส https://bot.example.com/close?token=abc123xyz&conv=5",
            f"ปิดเคส https://bot.example.com/close?token={TOKEN}&conv=5",
        ),
        (
            "https://bot.example.com/close/Xk3_9fQzLmN2pR7sT1uW4yZ6aB8cD0eF5gJiKlOqSvA",
            f"https://bot.example.com/close/{TOKEN}",
        ),
        ("https://x.example.com/p?email=malee@gmail.com", f"https://x.example.com/p?email={EMAIL}"),
    ],
)
def test_secret(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # รหัสรุ่น
        "ตู้เย็น Samsung RT38K5032S8/ST ยังมีของไหมคะ",
        "ขอดูรุ่นGR-B22KPหน่อยค่ะ",
        "รุ่น NR-BX471WGKT กับ GN-B392PLGK ต่างกันยังไง",
        "รหัส GR-1234567890",
        "SKU_0812345678",
        # ราคา
        "ราคา 12,990 บาท",
        "ลดเหลือ ฿8,900 ค่ะ",
        "งบ12990บาทค่ะ",
        "ราคา 1,234,567 บาท",
        "ช่วงราคา 12990-15990 บาท",
        "ราคา 1290 1590 1890 2190 บาท",
        "ราคา 12990.00",
        # ขนาด จำนวน ปี วันที่ เวลา
        "ตู้เย็น 9.1 คิว 2 ประตู",
        "อยากได้รุ่นปี 2026 ค่ะ",
        "ส่งวันที่ 22/10/2569 เวลา 10.30 น.",
        "ส่งของวันที่ 2026-10-22",
        "สั่ง 3 เครื่อง",
        "ห้อง 0912",
        "ขนาด 60x70x180 ซม.",
        # เลขยาวที่ไม่ผ่าน checksum/รูปแบบ
        "บาร์โค้ด 8850999320014",
        "4111 1111 1111 1112",
        # id ที่ log ต้องใช้ตามรอย
        "ai_run_id 550e8400-e29b-41d4-a716-446655440000",
        "user U4af4980629b60d432864f44d079f6d8e",
        # คำอังกฤษ / ลิงก์สินค้า
        "Bearer of good news",
        "keyboard: ไร้สาย",
        "https://shop.example.com/products/samsung-rt38k5032s8-st?sku=1234567890",
        "สวัสดีค่ะ ต้องการให้ช่วยหาสินค้าอะไรคะ",
        "",
    ],
)
def test_product_data_untouched(raw: str) -> None:
    assert redact_text(raw) == raw


def test_mixed_sentence() -> None:
    raw = (
        "สนใจตู้เย็น RT38K5032S8/ST ราคา 12,990 บาท 9.1 คิว "
        "ติดต่อ0812345678หรือ malee@gmail.com บัตร4111111111111111"
    )
    assert redact_text(raw) == (
        "สนใจตู้เย็น RT38K5032S8/ST ราคา 12,990 บาท 9.1 คิว "
        f"ติดต่อ{PHONE}หรือ {EMAIL} บัตร{CARD}"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "เบอร์0812345678ค่ะ อีเมล a@b.co บัตร 4111-1111-1111-1111 บัญชี 123-4-56789-0",
        f"token={LINE_TOKEN} Bearer {JWT} id {VALID_ID}",
        "https://bot.example.com/close?token=abc123xyz&x=1",
    ],
)
def test_idempotent(raw: str) -> None:
    once = redact_text(raw)
    assert redact_text(once) == once
    assert once != raw


def test_redact_obj_nested() -> None:
    obj = {
        "text_redacted": "เบอร์0812345678ค่ะ",
        "recent": [{"role": "user", "text": "อีเมล a@b.co"}, ("โอน 1234567890", 5)],
        "price": 12990,
        "flags": {"state_expired": True, "note": None},
        "0812345678": "key ไม่แตะ",
    }
    assert redact_obj(obj) == {
        "text_redacted": f"เบอร์{PHONE}ค่ะ",
        "recent": [{"role": "user", "text": f"อีเมล {EMAIL}"}, (f"โอน {ACCOUNT}", 5)],
        "price": 12990,
        "flags": {"state_expired": True, "note": None},
        "0812345678": "key ไม่แตะ",
    }


def test_logging_uses_redact() -> None:
    """app/logging.py import redact_text แบบ lazy — ต้องได้ฟังก์ชันนี้จริง"""
    from app.logging import _get_redact_text

    assert _get_redact_text() is redact_text

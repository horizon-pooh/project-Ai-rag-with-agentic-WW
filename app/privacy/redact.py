"""app/privacy/redact.py

redact ข้อความ: ปิดข้อมูลส่วนบุคคลก่อนส่ง AI API และก่อนลง log

รายละเอียด:
 - ปิด: เบอร์โทร, อีเมล, เลขบัตรประชาชน, เลขบัญชี/บัตร, ที่อยู่ (เท่าที่จับได้)
 - ปิด secret: token, key, replyToken
 - ผลเป็น text_redacted ที่เก็บใน messages และส่ง AI
 - ทุกรูปแบบต้องมีเคสทดสอบภาษาไทยจริง (ไทยไม่มีช่องว่างคั่นคำ จับพลาดง่าย)

แนวคิดจาก: src/observability/redact.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (redact_text, redact_obj) — ที่อยู่ยังไม่ปิด (ดูหมายเหตุด้านล่าง)

ข้อตัดสิน (งาน 3):
 - ตัวแทน: [PHONE] [EMAIL] [NATIONAL_ID] [CARD] [ACCOUNT] [TOKEN] (ไม่มีตัวเลขในตัวแทน -> redact ซ้ำได้ผลเดิม)
 - ตัวเลขจับเป็น "ก้อน" = กลุ่มเลขคั่นด้วยช่องว่าง/ขีด/จุด ตัวเดียว ติดภาษาไทยได้ ("เบอร์0812345678ค่ะ")
   แต่ไม่จับเลขที่ติดตัวอักษรอังกฤษ หรือต่อจากอักษร/เลขด้วย - หรือ _ (รหัสรุ่น เช่น RT38K5032S8/ST, GR-B22KP)
   ก้อนที่ไม่เข้ารูปทั้งก้อน -> ลองช่วงย่อย (ยาวสุดก่อน ซ้ายไปขวา) เช่น "12990 0812345678" ปิดแค่เบอร์
 - เบอร์โทร: ขึ้นต้น 0 ยาว 10 หลักและหลักที่ 2 เป็น 6/8/9 (มือถือ) หรือ 9 หลักและหลักที่ 2 เป็น 2-7 (บ้าน)
   หรือ +66/66 ตามด้วยเลขไม่มี 0 นำ (9 หรือ 8 หลัก); + ตามด้วยเลข 8-15 หลักอื่น = เบอร์ต่างประเทศ
 - บัตรประชาชน: 13 หลักรูป x-xxxx-xxxxx-xx-x -> ปิดเสมอ (รูปนี้ชัดพอ แม้ checksum ผิดเพราะพิมพ์พลาด)
   13 หลักติดกัน -> ต้องผ่าน checksum (ลด false positive จากบาร์โค้ด EAN-13 / เลขยาวอื่น)
 - บัตรเครดิต: 13-19 หลัก ต้องผ่าน Luhn + ขึ้นต้นตามเครือข่าย (Visa/MC/Amex/JCB/UnionPay/Diners/Discover)
   รูป: ติดกัน, กลุ่มละ 4 (กลุ่มท้าย 1-4), หรือ 4-6-5 / 4-6-4
 - เลขบัญชี: 10-12 หลักติดกัน (ที่ไม่ใช่เบอร์โทร) หรือรูป xxx-x-xxxxx-x / xxx-x-xxxxx-xxx
   รูปอื่นที่คั่นช่องว่าง (เช่น 3-3-4, 4-4-4) ไม่จับ เพราะชนกับรายการราคา "1290 1590 1890"
   ผลข้างเคียงที่ยอมรับ: SKU/เลขอ้างอิงที่เป็นตัวเลขล้วน 10-12 หลักจะถูกปิดเป็น [ACCOUNT]
 - secret: "Bearer xxx", key=value ที่ชื่อ key เป็น token/secret/key/password/signature (ค่า >= 8 ตัว),
   ก้อนอักษรไม่มีช่องว่าง >= 40 ตัวที่มีทั้งตัวใหญ่ ตัวเล็ก และเลข (LINE channel token, JWT, token_urlsafe)
   หรือ hex >= 32 ตัวที่มีทั้งเลขและอักษร (replyToken, channel secret)
   ไม่จับ LINE userId (U + hex 32) และ UUID เพราะต้องใช้ตามรอยใน log
 - URL: ไม่แตะ host/path ยกเว้น segment ที่ดูเป็น secret; query ที่ชื่อเป็น secret -> [TOKEN]; ค่าอื่นใน query
   ปิดแค่อีเมลและ secret (ไม่ปิดตัวเลข เพราะลิงก์สินค้ามักมีรหัสตัวเลข)
 - ที่อยู่: ไม่ปิด — ภาษาไทยไม่มีรูปที่จับได้แน่นอน (บ้านเลขที่ "12/3", รหัสไปรษณีย์ 5 หลัก ชนกับราคา/รุ่น)
   เดาแล้วจะปิดข้อมูลสินค้าผิดมากกว่าที่ได้ ถ้าต้องการต้องตัดสินใหม่ (เช่น ปิดหลังคำว่า "ที่อยู่")
 - ชื่อคนไม่ปิด (จับไม่ได้แน่นอน)
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PHONE = "[PHONE]"
EMAIL = "[EMAIL]"
NATIONAL_ID = "[NATIONAL_ID]"
CARD = "[CARD]"
ACCOUNT = "[ACCOUNT]"
TOKEN = "[TOKEN]"

# เลขไทย ๐-๙ นับเป็นตัวเลขด้วย
_DIGIT = "0-9๐-๙"
_ALNUM = f"{_DIGIT}A-Za-z"
_TO_ASCII = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# ตัวคั่นระหว่างกลุ่มเลข: ช่องว่างตัวเดียว หรือ - / . (มีช่องว่างข้างละไม่เกินหนึ่ง)
_SP = "[  ]"
_SEP = rf"(?:{_SP}?[\-.]{_SP}?|{_SP})"
_NUM_RUN = re.compile(
    # ห้ามต่อจากอักษร/เลข และห้ามต่อจาก "X-" / "X_" (รหัสรุ่น, uuid)
    rf"(?<![{_ALNUM}])(?<![{_ALNUM}][\-_])"
    rf"(?:\+{_SP}?)?"
    rf"(?:\([{_DIGIT}]{{1,4}}\){_SP}?-?)?"
    rf"[{_DIGIT}]+(?:{_SEP}[{_DIGIT}]+)*"
    rf"(?![{_ALNUM}])(?![\-_][{_ALNUM}])"
)
_DIGIT_GROUP = re.compile(rf"[{_DIGIT}]+")
_MAX_DIGITS = 19

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)
_BEARER_RE = re.compile(r"(?<![A-Za-z])(bearer)\s+[A-Za-z0-9\-._~+/]{16,}=*", re.IGNORECASE)
_SECRET_KEY_WORDS = ("token", "secret", "key", "password", "signature")
_KEY_VALUE_RE = re.compile(
    r"(?<![A-Za-z])([A-Za-z_\-]*?(?:token|secret|key|password|signature))"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s\"'&,;]{8,})",
    re.IGNORECASE,
)
_TOKEN_CHARS = r"A-Za-z0-9+/=._\-"
_TOKEN_CANDIDATE = re.compile(rf"(?<![{_TOKEN_CHARS}])[{_TOKEN_CHARS}]{{32,}}(?![{_TOKEN_CHARS}])")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
# URL หยุดที่ช่องว่าง เครื่องหมายคำพูด และอักษรไทย (ลูกค้าพิมพ์ลิงก์ติดภาษาไทย)
_URL_RE = re.compile(r"https?://[^\s<>\"'฀-๿]+", re.IGNORECASE)

# ขึ้นต้นเลขบัตรตามเครือข่าย
_CARD_PREFIXES = ("4", "51", "52", "53", "54", "55", "34", "37", "35", "62", "6011", "65", "36", "30", "38")


def _is_secret_like(s: str) -> bool:
    """ก้อนอักษรยาวที่ดูเป็น token/key (ไม่ใช่คำหรือรหัสรุ่น)"""
    has_digit = any(c.isdigit() for c in s)
    if len(s) >= 32 and _HEX_RE.fullmatch(s) and has_digit and any(c.isalpha() for c in s):
        return True
    return (
        len(s) >= 40
        and has_digit
        and any(c.isupper() for c in s)
        and any(c.islower() for c in s)
    )


def _thai_id_ok(d: str) -> bool:
    total = sum(int(d[i]) * (13 - i) for i in range(12))
    return (11 - total % 11) % 10 == int(d[12])


def _luhn_ok(d: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card_shape_ok(groups: tuple[int, ...]) -> bool:
    if len(groups) == 1:
        return True
    if groups in ((4, 6, 5), (4, 6, 4)):
        return True
    return len(groups) >= 3 and all(g == 4 for g in groups[:-1]) and 1 <= groups[-1] <= 4


def _is_card_prefix(d: str) -> bool:
    return d.startswith(_CARD_PREFIXES) or 2221 <= int(d[:4]) <= 2720


def _classify(d: str, groups: tuple[int, ...], plus: bool) -> str | None:
    """d = เลขล้วน (ASCII), groups = ความยาวแต่ละกลุ่ม, plus = มี + นำหน้า"""
    n = len(d)
    # เบอร์โทรไทยแบบ +66 / 66
    if d.startswith("66") and (plus or n in (10, 11)):
        rest = d[2:]
        if rest.startswith("0"):
            rest = rest[1:]
        if (len(rest) == 9 and rest[0] in "689") or (len(rest) == 8 and rest[0] in "234567"):
            return PHONE
    if plus and 8 <= n <= 15:
        return PHONE
    # เบอร์โทรไทยขึ้นต้น 0 (รูปคั่นแบบไหนก็ได้)
    if d[0] == "0" and (
        (n == 10 and d[1] in "689") or (n == 9 and d[1] in "234567")
    ):
        return PHONE
    if n == 13:
        if groups == (1, 4, 5, 2, 1):
            return NATIONAL_ID
        if len(groups) == 1 and _thai_id_ok(d):
            return NATIONAL_ID
    if 13 <= n <= 19 and _card_shape_ok(groups) and _is_card_prefix(d) and _luhn_ok(d):
        return CARD
    if 10 <= n <= 12 and (len(groups) == 1 or groups in ((3, 1, 5, 1), (3, 1, 5, 3))):
        return ACCOUNT
    return None


def _redact_number_run(m: re.Match[str]) -> str:
    run = m.group(0)
    plus = run.startswith("+")
    spans = [(g.start(), g.end()) for g in _DIGIT_GROUP.finditer(run)]
    digits = [run[a:b].translate(_TO_ASCII) for a, b in spans]
    out: list[str] = []
    pos = 0  # ตำแหน่งใน run ที่คัดลอกออกไปแล้ว
    i = 0
    while i < len(spans):
        # ช่วงย่อย i..j ที่รวมแล้วไม่เกิน 19 หลัก ลองยาวสุดก่อน
        ends: list[int] = []
        count = 0
        for j in range(i, len(spans)):
            count += len(digits[j])
            if count > _MAX_DIGITS:
                break
            ends.append(j)
        label = None
        for j in reversed(ends):
            label = _classify(
                "".join(digits[i : j + 1]),
                tuple(len(x) for x in digits[i : j + 1]),
                plus and i == 0,
            )
            if label:
                start = 0 if i == 0 else spans[i][0]
                out.append(run[pos:start])
                out.append(label)
                pos = spans[j][1]
                i = j + 1
                break
        if not label:
            i += 1
    out.append(run[pos:])
    return "".join(out)


def _redact_key_value(m: re.Match[str]) -> str:
    return f"{m.group(1)}{m.group(2)}{TOKEN}"


def _redact_token_candidate(m: re.Match[str]) -> str:
    return TOKEN if _is_secret_like(m.group(0)) else m.group(0)


def _redact_plain(s: str) -> str:
    """ข้อความที่ไม่ใช่ URL"""
    s = _BEARER_RE.sub(lambda m: f"{m.group(1)} {TOKEN}", s)
    s = _KEY_VALUE_RE.sub(_redact_key_value, s)
    s = _EMAIL_RE.sub(EMAIL, s)
    s = _TOKEN_CANDIDATE.sub(_redact_token_candidate, s)
    return _NUM_RUN.sub(_redact_number_run, s)


def _redact_query_value(v: str) -> str:
    if _is_secret_like(v):
        return TOKEN
    return _EMAIL_RE.sub(EMAIL, v)


def _redact_url(url: str) -> str:
    base, hash_sign, fragment = url.partition("#")
    base, q_sign, query = base.partition("?")
    scheme, sep, rest = base.partition("://")
    host, slash, path = rest.partition("/")
    if path:
        path = "/".join(TOKEN if _is_secret_like(seg) else seg for seg in path.split("/"))
    params = []
    for part in query.split("&") if query else []:
        key, eq, value = part.partition("=")
        if eq and any(w in key.casefold() for w in _SECRET_KEY_WORDS):
            value = TOKEN
        elif eq:
            value = _redact_query_value(value)
        params.append(f"{key}{eq}{value}")
    if fragment:
        fragment = _redact_query_value(fragment)
    return f"{scheme}{sep}{host}{slash}{path}{q_sign}{'&'.join(params)}{hash_sign}{fragment}"


def redact_text(s: str) -> str:
    """ปิดข้อมูลส่วนบุคคลและ secret ในข้อความ (redact ซ้ำได้ผลเดิม)"""
    if not s:
        return s
    out: list[str] = []
    pos = 0
    for m in _URL_RE.finditer(s):
        out.append(_redact_plain(s[pos : m.start()]))
        out.append(_redact_url(m.group(0)))
        pos = m.end()
    out.append(_redact_plain(s[pos:]))
    return "".join(out)


def redact_obj(obj: Any) -> Any:
    """redact ทุก string ใน dict/list/tuple ซ้อนกัน (เช่น input ของ ai_steps) — key ของ dict ไม่แตะ
    ค่าที่ไม่ใช่ string (ตัวเลข, bool, None) ผ่านไปตรงๆ; pydantic model ให้ model_dump() ก่อน
    """
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, Mapping):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v) for v in obj)
    return obj

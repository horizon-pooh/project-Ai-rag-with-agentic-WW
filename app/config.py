"""app/config.py

อ่าน env ทั้งหมดที่จุดเดียว ครั้งเดียวตอนเริ่ม ขาดค่าบังคับเมื่อไหร่หยุดทันที (fail fast)

รายละเอียด:
 - ใช้ pydantic-settings อ่านจาก .env (รายการตัวแปรดู .env.example)
 - บังคับ: LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, DATABASE_URL, REDIS_URL
 - ห้ามข้ามการตรวจ signature ไม่ว่าโหมดไหน
 - AI_TIMEOUT_S, AI_CONCURRENCY, TURN_DEADLINE_S: ค่าจริงรอคำตอบเรื่อง AI API
 - STATE_TTL_HOURS=2, HANDOFF_TTL_HOURS=6
 - token / key อยู่ที่นี่ที่เดียว ห้ามส่งเข้า AI API ห้ามลง log

แนวคิดจาก: src/config/index.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (Settings + get_settings) — ค่าที่ติด (เสนอ) ยังไม่ได้ยืนยัน
"""

from __future__ import annotations

import re
from datetime import time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HOURS_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    """ค่าตั้งทั้งหมดของระบบ ชื่อฟิลด์ = ชื่อ env ตัวเล็ก (เช่น LINE_CHANNEL_SECRET -> line_channel_secret)

    - env ที่เป็นค่าว่าง (เช่น `AI_TIMEOUT_S=` ใน .env.example) ถือว่าไม่ได้ตั้ง -> ใช้ค่าตั้งต้น
      ค่าบังคับที่ว่าง = ขาด -> ValidationError ตอนเริ่ม
    - secret เป็น SecretStr: repr/log เห็นเป็น ********** ต้องเรียก .get_secret_value() เอง
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # --- LINE (บังคับ) ---
    line_channel_secret: SecretStr
    line_channel_access_token: SecretStr

    # --- ที่เก็บข้อมูล (บังคับ) — URL อาจมีรหัสผ่าน จึงเป็น SecretStr ---
    database_url: SecretStr
    redis_url: SecretStr

    # --- AI API ภายนอก ---
    # ว่าง -> แอปใช้ AI client แบบ stub (ยังไม่มี API จริง) — รอคำตอบเรื่อง AI API
    ai_api_url: str | None = None
    ai_api_key: SecretStr | None = None
    # (เสนอ) รอคำตอบเรื่อง AI API
    ai_timeout_s: float = Field(default=30, gt=0)
    # (เสนอ) รอคำตอบเรื่อง AI API
    ai_concurrency: int = Field(default=4, ge=1)
    # (เสนอ) เพดานเวลารวมต่อข้อความ รวมทุกรอบที่ลองซ้ำ — รอคำตอบเรื่อง AI API
    turn_deadline_s: float = Field(default=50, gt=0)

    # --- MinIO ---
    minio_endpoint: str = "minio:9000"
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    # (เสนอ)
    minio_bucket: str = "line-images"
    # (เสนอ) ใน docker compose คุยกันด้วย http
    minio_secure: bool = False

    # --- state / ส่งต่อพนักงาน ---
    state_ttl_hours: float = Field(default=2, gt=0)
    # นับจาก handoff_started_at
    handoff_ttl_hours: float = Field(default=6, gt=0)
    # (เสนอ) อายุ replyToken ที่เราเชื่อ (LINE ~1 นาที ต้องเช็คเอกสาร)
    reply_token_ttl_s: int = Field(default=50, gt=0)
    # (เสนอ) clarify ติดกันครบเท่านี้แล้วยัง clarify -> ส่งต่อ
    clarify_max: int = Field(default=2, ge=1)
    # (เสนอ) ข้อความต่อนาทีต่อ user เกินนี้ = ถี่ผิดปกติ
    rate_limit_per_min: int = Field(default=10, ge=1)
    # อายุลิงก์ /close
    close_token_ttl_hours: float = Field(default=24, gt=0)
    # ใช้สร้างลิงก์ /close เช่น https://bot.example.com (ไม่มี / ท้าย)
    public_base_url: str | None = None

    # --- retention (docs/Line_ai_bot.md หัวข้อ 8) — raw 90 วันอยู่ใน worker/cron.py ---
    # (เสนอ) รูปเก็บสั้นกว่าข้อความ
    image_retention_days: int = Field(default=30, ge=1)
    # ข้อความและ trace เกินนี้ -> anonymize
    trace_retention_months: int = Field(default=12, ge=1)

    # --- แจ้ง call center (ช่องทางยังไม่ได้เลือก) ---
    callcenter_notify_channel: str | None = None
    callcenter_notify_target: str | None = None
    # เวลาทำการ "HH:MM-HH:MM" ตาม timezone (ว่าง = ไม่รู้เวลาทำการ)
    callcenter_hours: str | None = None
    timezone: str = "Asia/Bangkok"

    # --- ทางหลบ (OR กับ ops_flags ใน DB) ---
    kill_switch: bool = False

    # --- log และแจ้งเตือน ---
    log_level: str = "INFO"
    sentry_dsn: SecretStr | None = None

    # --- ไฟล์กฎ ---
    rules_dir: Path = Path("prompts/rules")

    @field_validator("public_base_url", "ai_api_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @field_validator("callcenter_hours")
    @classmethod
    def _check_hours(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not _HOURS_RE.match(v):
            raise ValueError("CALLCENTER_HOURS ต้องเป็นรูป HH:MM-HH:MM เช่น 09:00-18:00")
        return v

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"TIMEZONE ไม่รู้จัก: {v}") from e
        return v

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL ต้องเป็นหนึ่งใน {sorted(_LOG_LEVELS)}")
        return v

    # --- ค่าที่แปลงแล้ว (ใช้แทนตัวเลขดิบ) ---

    @property
    def state_ttl(self) -> timedelta:
        return timedelta(hours=self.state_ttl_hours)

    @property
    def handoff_ttl(self) -> timedelta:
        return timedelta(hours=self.handoff_ttl_hours)

    @property
    def close_token_ttl(self) -> timedelta:
        return timedelta(hours=self.close_token_ttl_hours)

    @property
    def image_retention(self) -> timedelta:
        return timedelta(days=self.image_retention_days)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def ai_enabled(self) -> bool:
        """False เมื่อไม่ได้ตั้ง AI_API_URL -> ใช้ AI client แบบ stub"""
        return bool(self.ai_api_url)

    @property
    def callcenter_hours_range(self) -> tuple[time, time] | None:
        """(เริ่ม, เลิก) ของเวลาทำการ ตาม timezone; None ถ้าไม่ได้ตั้ง"""
        if not self.callcenter_hours:
            return None
        start, end = self.callcenter_hours.split("-")
        return time.fromisoformat(start), time.fromisoformat(end)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings ตัวเดียวของ process (อ่าน env ครั้งแรกครั้งเดียว) เทสต์ล้างด้วย get_settings.cache_clear()"""
    return Settings()  # type: ignore[call-arg]  # ค่าบังคับมาจาก env

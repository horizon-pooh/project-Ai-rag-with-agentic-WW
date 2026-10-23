"""tests/test_config.py — app/config.py: ขาดค่าบังคับต้องหยุดทันที, ค่าตั้งต้นตรงกับ docs/PLAN.md"""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings

REQUIRED = {
    "LINE_CHANNEL_SECRET": "s",
    "LINE_CHANNEL_ACCESS_TOKEN": "t",
    "DATABASE_URL": "postgresql://u:p@db:5432/x",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_env_fails(clean_env: pytest.MonkeyPatch, missing: str) -> None:
    for k, v in REQUIRED.items():
        if k != missing:
            clean_env.setenv(k, v)
    with pytest.raises(ValidationError) as exc:
        _settings()
    assert missing.lower() in str(exc.value)


def test_empty_required_env_counts_as_missing(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("LINE_CHANNEL_SECRET", "")
    with pytest.raises(ValidationError):
        _settings()


def test_defaults(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    # ค่าว่างแบบใน .env.example -> ใช้ค่าตั้งต้น
    clean_env.setenv("AI_TIMEOUT_S", "")
    s = _settings()
    assert s.ai_timeout_s == 30
    assert s.ai_concurrency == 4
    assert s.turn_deadline_s == 50
    assert s.ai_api_url is None and s.ai_api_key is None and not s.ai_enabled
    assert s.state_ttl == timedelta(hours=2)
    assert s.handoff_ttl == timedelta(hours=6)
    assert s.close_token_ttl == timedelta(hours=24)
    assert s.reply_token_ttl_s == 50
    assert s.clarify_max == 2
    assert s.rate_limit_per_min == 10
    assert s.kill_switch is False
    assert s.timezone == "Asia/Bangkok"
    assert s.log_level == "INFO"
    assert s.rules_dir == Path("prompts/rules")
    assert s.callcenter_hours_range is None


def test_secrets_are_hidden(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("LINE_CHANNEL_ACCESS_TOKEN", "very-secret-token")
    s = _settings()
    assert "very-secret-token" not in repr(s)
    assert s.line_channel_access_token.get_secret_value() == "very-secret-token"


def test_parsed_values(clean_env: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv("CALLCENTER_HOURS", "09:00-18:00")
    clean_env.setenv("PUBLIC_BASE_URL", "https://bot.example.com/")
    clean_env.setenv("KILL_SWITCH", "true")
    clean_env.setenv("AI_API_URL", "https://ai.example.com/v1")
    s = _settings()
    assert s.callcenter_hours_range == (time(9, 0), time(18, 0))
    assert s.public_base_url == "https://bot.example.com"
    assert s.kill_switch is True
    assert s.ai_enabled


@pytest.mark.parametrize(
    ("name", "value"),
    [("CALLCENTER_HOURS", "9-18"), ("TIMEZONE", "Mars/Olympus"), ("LOG_LEVEL", "LOUD"), ("AI_CONCURRENCY", "0")],
)
def test_invalid_values_fail(clean_env: pytest.MonkeyPatch, name: str, value: str) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.setenv(name, value)
    with pytest.raises(ValidationError):
        _settings()


def test_get_settings_is_cached(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k, v in REQUIRED.items():
        clean_env.setenv(k, v)
    clean_env.chdir(tmp_path)  # ไม่ให้ .env ในเครื่องมาปน
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_env_example_lists_every_setting() -> None:
    names = {
        line.split("=", 1)[0].strip()
        for line in Path(".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    missing = {f.upper() for f in Settings.model_fields} - names
    assert not missing, f"ต้องเพิ่มใน .env.example: {sorted(missing)}"

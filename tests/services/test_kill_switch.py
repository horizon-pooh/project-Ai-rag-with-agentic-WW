"""tests/services/test_kill_switch.py

เคสทดสอบของ app/services/kill_switch.py

รายละเอียด:
 - env KILL_SWITCH=true -> เปิดเสมอ (ไม่อ่าน DB)
 - ops_flags 'kill_switch' = 'on' -> เปิด; 'off' / ไม่มี / ค่าอื่น -> ปิด
 - อ่านค่าตอนรันทุกครั้ง (เปลี่ยน flag แล้วเห็นทันที)
 - อ่าน flag ไม่ได้ -> เปิด (fail-safe) และ log
 - set_kill_switch เขียน 'on' / 'off'

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

import pytest

from app.pipeline.ports import KillSwitch
from app.services import kill_switch as kill_switch_mod
from app.services.kill_switch import FLAG_KEY, FlagsKillSwitch, set_kill_switch
from tests.fakes import FakeClock, FakeDb, FakeFlagsRepo, make_settings
from tests.services._helpers import RecordingLogger


@pytest.fixture
def logger(monkeypatch) -> RecordingLogger:
    rec = RecordingLogger()
    monkeypatch.setattr(kill_switch_mod, "log", rec)
    return rec


@pytest.fixture
def flags() -> FakeFlagsRepo:
    return FakeFlagsRepo(FakeDb(FakeClock()))


class BrokenFlags:
    def __init__(self) -> None:
        self.calls = 0

    async def get_flag(self, key: str) -> str | None:
        self.calls += 1
        raise ConnectionError("db down")

    async def set_flag(self, key: str, value: str) -> None:
        raise ConnectionError("db down")


def test_implements_port(flags):
    assert isinstance(FlagsKillSwitch(make_settings(), flags), KillSwitch)


async def test_off_by_default(flags, logger):
    assert await FlagsKillSwitch(make_settings(), flags).is_on() is False


async def test_env_on_wins_without_reading_db(logger):
    broken = BrokenFlags()
    ks = FlagsKillSwitch(make_settings(kill_switch=True), broken)
    assert await ks.is_on() is True
    assert broken.calls == 0


@pytest.mark.parametrize(("value", "expected"), [("on", True), (" ON ", True), ("off", False), ("", False), ("yes", False)])
async def test_flag_values(flags, logger, value, expected):
    await flags.set_flag(FLAG_KEY, value)
    assert await FlagsKillSwitch(make_settings(), flags).is_on() is expected


async def test_read_at_runtime_every_call(flags, logger):
    ks = FlagsKillSwitch(make_settings(), flags)
    assert await ks.is_on() is False
    await set_kill_switch(flags, True)
    assert await flags.get_flag(FLAG_KEY) == "on"
    assert await ks.is_on() is True
    await set_kill_switch(flags, False)
    assert await flags.get_flag(FLAG_KEY) == "off"
    assert await ks.is_on() is False


async def test_env_on_cannot_be_turned_off_by_flag(flags, logger):
    ks = FlagsKillSwitch(make_settings(kill_switch=True), flags)
    await set_kill_switch(flags, False)
    assert await ks.is_on() is True


async def test_read_error_fails_safe_on_and_logs(logger):
    ks = FlagsKillSwitch(make_settings(), BrokenFlags())
    assert await ks.is_on() is True
    assert "kill_switch_read_failed" in logger.events()

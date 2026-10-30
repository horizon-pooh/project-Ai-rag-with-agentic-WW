"""tests/worker/test_watchdog.py

เคสทดสอบของ app/worker/watchdog.py (ไม่ต้องมี Postgres — probe / alerter / เวลาเป็นของปลอม)

รายละเอียด:
 - detect: DB ล่ม / ไม่มี worker / heartbeat เก่า / pending ค้าง / ปกติ
 - AlertTracker: ไม่แจ้งก่อนครบ grace, แจ้งครั้งเดียวแล้วซ้ำตาม repeat, แจ้งหายเฉพาะที่เคยแจ้ง,
   ปัญหาที่หายก่อนครบ grace ไม่แจ้งอะไร, ตอน DB ล่มไม่แจ้งว่าปัญหาอื่นหาย
 - run_watchdog: deploy ปกติ (ไม่มี worker 1 รอบ) ไม่แจ้ง, worker ตายจริงแจ้งแล้วหายแล้วแจ้งหาย
 - ข้อความแจ้งไม่มีข้อมูลลูกค้า (มีแค่รหัส จำนวน อายุ)

แนวคิดจาก: ใหม่ (D-012)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.repositories.health import QueueHealth
from app.worker import watchdog as wd
from app.worker.watchdog import AlertTracker, Notice, detect, format_notice, run_watchdog
from tests.fakes import DEFAULT_NOW, make_settings

OK = QueueHealth(workers=1, heartbeat_age_s=5.0, oldest_pending_age_s=None)
NO_WORKER = QueueHealth(workers=0, heartbeat_age_s=None, oldest_pending_age_s=None)
STALE = QueueHealth(workers=2, heartbeat_age_s=500.0, oldest_pending_age_s=900.0)


def settings(**kw):
    values = {
        "watchdog_interval_s": 60,
        "watchdog_grace_s": 180,
        "watchdog_heartbeat_stale_s": 120,
        "watchdog_pending_stale_s": 600,
        "watchdog_repeat_s": 3600,
    }
    values.update(kw)
    return make_settings(**values)


# --- detect ---


def test_detect_ok():
    assert detect(OK, settings()) == {}


def test_detect_db_unreachable():
    assert set(detect(None, settings())) == {wd.DB_UNREACHABLE}


def test_detect_no_worker():
    assert set(detect(NO_WORKER, settings())) == {wd.NO_WORKER}


def test_detect_stale_heartbeat_and_pending():
    problems = detect(STALE, settings())
    assert set(problems) == {wd.WORKER_STALE, wd.PENDING_STUCK}
    assert "500" in problems[wd.WORKER_STALE]
    assert "15 นาที" in problems[wd.PENDING_STUCK]


def test_detect_thresholds_are_exclusive():
    at_limit = QueueHealth(workers=1, heartbeat_age_s=120.0, oldest_pending_age_s=600.0)
    assert detect(at_limit, settings()) == {}


def test_detect_pending_stuck_even_with_live_worker():
    h = QueueHealth(workers=1, heartbeat_age_s=3.0, oldest_pending_age_s=601.0)
    assert set(detect(h, settings())) == {wd.PENDING_STUCK}


# --- AlertTracker ---


def problem(code: str = wd.NO_WORKER) -> dict[str, str]:
    return {code: "x"}


def test_tracker_waits_for_grace_then_alerts_once():
    t = AlertTracker(grace_s=180, repeat_s=3600)
    assert t.observe(0, problem()) == []
    assert t.observe(120, problem()) == []
    assert t.observe(180, problem()) == [Notice("problem", wd.NO_WORKER, "x")]
    assert t.observe(240, problem()) == []


def test_tracker_repeats_after_repeat_interval():
    t = AlertTracker(grace_s=0, repeat_s=3600)
    assert len(t.observe(0, problem())) == 1
    assert t.observe(3599, problem()) == []
    assert len(t.observe(3600, problem())) == 1


def test_tracker_recovered_only_after_alert():
    t = AlertTracker(grace_s=180, repeat_s=3600)
    t.observe(0, problem())
    # หายก่อนครบ grace -> ไม่แจ้งอะไรเลย และเริ่มนับใหม่ถ้ากลับมา
    assert t.observe(60, {}) == []
    assert t.observe(120, problem()) == []
    assert t.observe(299, problem()) == []
    assert len(t.observe(300, problem())) == 1
    assert t.observe(360, {}) == [Notice("recovered", wd.NO_WORKER, "กลับมาปกติแล้ว")]
    assert t.observe(420, {}) == []


def test_tracker_unknown_codes_keep_state_during_db_outage():
    t = AlertTracker(grace_s=0, repeat_s=3600)
    assert len(t.observe(0, problem(wd.NO_WORKER))) == 1
    # DB ล่ม: ไม่รู้ว่า worker กลับมาหรือยัง -> ห้ามแจ้งว่าหาย
    notices = t.observe(60, problem(wd.DB_UNREACHABLE), unknown=wd.NEEDS_DB)
    assert [n.code for n in notices] == [wd.DB_UNREACHABLE]
    # DB กลับมา worker ก็กลับมา -> แจ้งหายทั้งสอง
    kinds = {(n.kind, n.code) for n in t.observe(120, {})}
    assert kinds == {("recovered", wd.NO_WORKER), ("recovered", wd.DB_UNREACHABLE)}


def test_tracker_independent_codes():
    t = AlertTracker(grace_s=0, repeat_s=3600)
    t.observe(0, {wd.WORKER_STALE: "a", wd.PENDING_STUCK: "b"})
    notices = t.observe(60, {wd.PENDING_STUCK: "b"})
    assert notices == [Notice("recovered", wd.WORKER_STALE, "กลับมาปกติแล้ว")]


# --- format_notice ---


def test_format_problem_has_hint_and_time():
    subject, body = format_notice(Notice("problem", wd.NO_WORKER, "ไม่มี worker"), DEFAULT_NOW)
    assert wd.NO_WORKER in subject
    assert "ไม่มี worker" in body
    assert "docker compose" in body
    assert "2026-10-22" in body


def test_format_recovered():
    subject, _ = format_notice(Notice("recovered", wd.NO_WORKER, "กลับมาปกติแล้ว"), DEFAULT_NOW)
    assert subject.startswith("หายแล้ว")


# --- run_watchdog ---


class FakeAlerter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, body: str) -> bool:
        self.sent.append((subject, body))
        return True


class Script:
    """probe + เวลาปลอม: แต่ละรอบคืนค่าถัดไปในรายการ, sleep เลื่อนเวลา"""

    def __init__(self, results: list[QueueHealth | None]) -> None:
        self.results = list(results)
        self.now = 0.0

    async def probe(self) -> QueueHealth | None:
        return self.results.pop(0)

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.now += s


async def run(results: list[QueueHealth | None], **kw) -> FakeAlerter:
    script = Script(results)
    alerter = FakeAlerter()
    await run_watchdog(
        settings(**kw),
        probe=script.probe,
        alerter=alerter,
        monotonic=script.monotonic,
        sleep=script.sleep,
        max_rounds=len(results),
    )
    return alerter


async def test_run_restart_shorter_than_grace_is_silent():
    # deploy: worker หายไป 2 นาทีแล้วกลับมา (grace 3 นาที)
    alerter = await run([OK, NO_WORKER, NO_WORKER, OK, OK])
    assert alerter.sent == []


async def test_run_dead_worker_alerts_then_recovers():
    alerter = await run([NO_WORKER] * 5 + [OK])
    subjects = [s for s, _ in alerter.sent]
    assert subjects == [f"ระบบตอบแชทมีปัญหา: {wd.NO_WORKER}", f"หายแล้ว: {wd.NO_WORKER}"]


async def test_run_db_down_alerts():
    alerter = await run([None] * 4)
    assert [s for s, _ in alerter.sent] == [f"ระบบตอบแชทมีปัญหา: {wd.DB_UNREACHABLE}"]


async def test_run_alert_has_no_customer_data():
    alerter = await run([STALE] * 4)
    text = " ".join(s + b for s, b in alerter.sent)
    assert "U" + "0" * 4 not in text  # ไม่มี line_user_id
    assert "worker 2 ตัว" in text


@pytest.mark.parametrize("bad", [0, -1])
def test_settings_reject_non_positive_interval(bad):
    with pytest.raises(ValueError):
        settings(watchdog_interval_s=bad)


def test_format_uses_given_time():
    at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=DEFAULT_NOW.tzinfo)
    _, body = format_notice(Notice("problem", wd.PENDING_STUCK, "ค้าง"), at)
    assert "2026-01-02 03:04:05" in body

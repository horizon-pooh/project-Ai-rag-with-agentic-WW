"""app/services/kill_switch.py

kill switch: ปิดบอทให้ call center รับทั้งหมด โดยไม่ต้อง deploy

รายละเอียด:
 - อ่านค่าตอนรัน (ไม่ใช่แค่ตอนเริ่ม) จาก DB (ops_flags); ค่าตั้งต้นจาก KILL_SWITCH
 - เช็คที่ steps/send เท่านั้น ไม่ใช่ที่ handoff_check
 - เปิดอยู่ -> ไม่ส่งข้อความบอท (event ยังบันทึกตามปกติ)
 - ต้องซ้อมเปิด/ปิดก่อนขึ้นจริง (runbook)

แนวคิดจาก: src/ops/killSwitch.js (แบบเก่า, D-006)
สถานะ: มีโค้ดแล้ว — FlagsKillSwitch (ops_flags 'kill_switch' ใน Postgres OR env KILL_SWITCH) + set_kill_switch
       อ่าน flag ไม่ได้ -> ถือว่าเปิด (fail-safe ตาม orchestrator งาน 6; ต่างจาก docstring ใน ports ที่เขียนว่า False)
"""

from __future__ import annotations

import structlog

from app.config import Settings
from app.pipeline.ports import FlagsRepo

log = structlog.get_logger(__name__)

FLAG_KEY = "kill_switch"
FLAG_ON = "on"
FLAG_OFF = "off"
# อ่าน flag ไม่ได้ (DB ล่ม ฯลฯ) -> ไม่รู้สถานะจริง เลือกเงียบไว้ก่อนให้ call center รับ
# (ค่าเริ่มต้นเมื่อพลาดต้องปลอดภัย: ไม่ส่งข้อความที่อาจไม่ควรส่ง — ถ้า DB ล่ม sendGuard ก็อ่านสถานะ conversation ไม่ได้อยู่แล้ว)
ON_READ_ERROR = True


class FlagsKillSwitch:
    """เปิดเมื่อ env KILL_SWITCH=true หรือ ops_flags['kill_switch'] == 'on' (ไม่สนตัวพิมพ์/ช่องว่าง) อ่านใหม่ทุกครั้ง"""

    def __init__(self, settings: Settings, flags: FlagsRepo) -> None:
        self._env_on = settings.kill_switch
        self._flags = flags

    async def is_on(self) -> bool:
        if self._env_on:
            return True
        try:
            value = await self._flags.get_flag(FLAG_KEY)
        except Exception:
            log.exception("kill_switch_read_failed", fail_safe_on=ON_READ_ERROR)
            return ON_READ_ERROR
        return value is not None and value.strip().casefold() == FLAG_ON


async def set_kill_switch(flags: FlagsRepo, on: bool) -> None:
    """เปิด/ปิดใน ops_flags (ไม่ต้อง deploy) — env KILL_SWITCH=true ยังบังคับเปิดอยู่ ปิดที่นี่ไม่ได้"""
    await flags.set_flag(FLAG_KEY, FLAG_ON if on else FLAG_OFF)
    log.warning("kill_switch_set", on=on)


__all__ = ["FLAG_KEY", "FLAG_OFF", "FLAG_ON", "ON_READ_ERROR", "FlagsKillSwitch", "set_kill_switch"]

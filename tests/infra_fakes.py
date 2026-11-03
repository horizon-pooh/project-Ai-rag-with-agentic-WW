"""tests/infra_fakes.py

fake ของโครงสร้างพื้นฐานที่ไม่อยู่ในพอร์ตของ pipeline: คิว procrastinate ในหน่วยความจำ และ orchestrator.run_turn ปลอม
— ใช้ในเทสต์ api / worker

รายละเอียด:
 - make_queue(): procrastinate App ตัวใหม่บน InMemoryConnector (ตัวทดสอบของ procrastinate เอง) — ใส่ App นี้ให้
   tasks.enqueue_event / cron ได้ตรง ๆ; ดูงานที่ใส่ได้ที่ connector.jobs (แถวแบบ procrastinate_jobs)
   InMemoryConnector ตรวจ queueing_lock ซ้ำ (เฉพาะงาน todo) เหมือน unique index ของจริง
 - FailingConnector: ใส่คิวไม่ได้ (จำลอง DB ล่มตอน defer)
 - queued(connector): สรุปงานที่ใส่ไว้เป็น (task_name, args, lock, queueing_lock) ตามลำดับ
 - FakeTurns: แทน orchestrator.run_turn — บันทึกลำดับ raw id, ยก error ตามที่ตั้งต่อ id

แนวคิดจาก: ใหม่ (งาน 8; D-010 เปลี่ยน fake Redis/arq เป็น InMemoryConnector)
สถานะ: มีโค้ดแล้ว
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from procrastinate import App
from procrastinate.exceptions import ConnectorException
from procrastinate.testing import InMemoryConnector

from app.pipeline.orchestrator import TurnOutcome
from app.pipeline.schemas import RawEvent


class FailingConnector(InMemoryConnector):
    """defer พังทุกครั้ง (เหมือน DB ต่อไม่ได้ตอนใส่คิว)"""

    async def defer_jobs_all(self, jobs: Any) -> Any:
        raise ConnectorException("db down")


def make_queue(*, fail: bool = False) -> tuple[App, InMemoryConnector]:
    connector: InMemoryConnector = FailingConnector() if fail else InMemoryConnector()
    return App(connector=connector), connector


def queued(connector: InMemoryConnector) -> list[tuple[str, dict[str, Any], str | None, str | None]]:
    return [
        (row["task_name"], row["args"], row["lock"], row["queueing_lock"])
        for _, row in sorted(connector.jobs.items())
    ]


def finish_all(connector: InMemoryConnector) -> None:
    """ทุกงานจบแล้ว (worker หยิบไปทำ) -> queueing_lock เดิมใส่ใหม่ได้"""
    for row in connector.jobs.values():
        row["status"] = "succeeded"


@dataclass
class FakeTurns:
    """แทน orchestrator.run_turn: errors[raw_id] = รายการ Exception ที่ยกทีละครั้ง"""

    calls: list[int] = field(default_factory=list)
    errors: dict[int, list[Exception]] = field(default_factory=dict)
    outcome: TurnOutcome = field(default_factory=lambda: TurnOutcome(route="ai_answer"))

    async def __call__(self, deps: Any, raw: RawEvent, cfg: Any) -> Any:
        self.calls.append(raw.id)
        queue = self.errors.get(raw.id)
        if queue:
            raise queue.pop(0)
        return self.outcome

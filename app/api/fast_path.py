"""app/api/fast_path.py

ทางด่วนของคำสั่งเปลี่ยนสถานะ ไม่รอล็อกต่อ user (เสนอ)

รายละเอียด:
 - ปัญหา: postback talk_to_staff ที่มาระหว่างรอ AI ต้องรอล็อก -> AI ตอบก่อน แล้วค่อยส่งต่อ (ช้าเป็นนาทีถ้า API ช้า)
 - คำสั่งที่วิ่งทางนี้: postback action=talk_to_staff, action=resume_ai, คำขอคนชัดเจนตามกฎขั้น 5
 - talk_to_staff: start_handoff ด้วย UPDATE ... WHERE status='bot' (ใครมาก่อนชนะ)
 - พอสถานะเป็น human แล้ว sendGuard จะทิ้งคำตอบ AI ที่มาช้า (= เคส escalationRace เดิม)
 - event เดิมยังเข้าคิวตามปกติ; handoff_check ต้องเห็นว่าทำไปแล้วและไม่ทำซ้ำ
 - ยังไม่ตัดสิน: เรียกจาก webhook ตรงๆ หรือคิวแยกที่ไม่ล็อก

ข้อตัดสินในงาน 8:
 - เรียกจาก webhook ตรงๆ เป็น BackgroundTask ของ FastAPI (หลังตอบ 200) ไม่มีคิวแยก
 - event ไหนวิ่งทางนี้ตัดสินโดย orchestrator.is_fast_path_event (webhook เรียก)
 - ทางนี้ claim event แบบเดียวกับ worker: ได้ก่อนคนนั้นทำ; worker ที่มาทีหลัง claim ได้ None แล้วข้าม
 - จบด้วย run_claimed ตัวเดียวกับ worker (done / release ให้ลองใหม่ / failed) — release แล้ว cron requeue_pending เก็บ

แนวคิดจาก: src/conversation/handlePostback.js, docs/diagrams/03-escalation-race.html (แนวคิดเดิม), จุดเรียกใหม่
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.logging import bind_ids, clear_ids
from app.pipeline.ports import Deps
from app.worker.tasks import run_claimed

if TYPE_CHECKING:
    from app.pipeline.orchestrator import PipelineConfig

log = structlog.get_logger(__name__)


async def run_fast_path(deps: Deps, raw_event_id: int, cfg: PipelineConfig) -> None:
    """claim แล้วรัน pipeline ทันทีโดยไม่รอล็อกต่อ user (BackgroundTask: ห้ามปล่อย error ออก)"""
    clear_ids()
    bind_ids(raw_event_id=raw_event_id)
    try:
        raw = await deps.raw_events.claim(raw_event_id)
        if raw is None:
            # worker ทำไปก่อนแล้ว
            log.info("fast_path_claim_skipped")
            return
        log.info("fast_path_started")
        await run_claimed(deps, raw, cfg)
    except Exception:
        # แถวอาจค้าง processing -> cron release_stuck เก็บ
        log.exception("fast_path_failed")
    finally:
        clear_ids()


__all__ = ["run_fast_path"]

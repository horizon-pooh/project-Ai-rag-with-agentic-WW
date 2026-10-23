"""app/pipeline/pdpa.py

ลบข้อมูลของลูกค้าหนึ่งคนตาม PDPA (docs/Line_ai_bot.md หัวข้อ 8) — ทางเดียวที่ลบ user

รายละเอียด:
 - delete_user_data(deps, line_user_id): UsersRepo.delete_user (transaction เดียว: raw events + line_users cascade)
   แล้วลบไฟล์ใน Storage ทีละ key
 - DB เป็นความจริง: ลบใน DB สำเร็จแล้วถือว่าลบแล้ว ลบไฟล์ไม่ได้ -> log + คืนใน failed_keys (ไม่ยก error กลับ)
   ให้ผู้ดูแลลบซ้ำเอง; DB พัง -> ยก error (ยังไม่ได้ลบอะไร)
 - line_user_id เป็นข้อมูลส่วนบุคคล: log แค่ user_ref = hash_token(line_user_id)[:12]
 - ไม่เขียน SQL ไม่เรียก LINE (ผ่าน deps เท่านั้น); ทางเข้าคือ app/worker/admin.py

แนวคิดจาก: ใหม่ (R2-A)
สถานะ: มีโค้ดแล้ว (R2-A) — ยังไม่กันกรณี webhook ของ user เดิมเข้ามาระหว่าง/หลังลบ (จะสร้าง user ใหม่)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.pipeline.ports import Deps
from app.pipeline.schemas import hash_token

log = structlog.get_logger(__name__)

USER_REF_CHARS = 12


def user_ref(line_user_id: str) -> str:
    """ตัวอ้างอิงใน log แทน line_user_id (hash ตัดสั้น ย้อนกลับไม่ได้)"""
    return hash_token(line_user_id)[:USER_REF_CHARS]


@dataclass(frozen=True)
class PdpaResult:
    """ผลการลบ: user_found=False -> ไม่มี user ใน line_users (raw events ของ id นี้ถูกลบแล้วถ้ามี)"""

    user_found: bool
    files_deleted: int = 0
    failed_keys: list[str] = field(default_factory=list)


async def delete_user_data(deps: Deps, line_user_id: str) -> PdpaResult:
    """ลบข้อมูลทั้งหมดของ line_user_id ใน DB แล้วลบไฟล์ใน Storage"""
    if not line_user_id:
        raise ValueError("line_user_id ว่าง")
    ref = user_ref(line_user_id)
    keys = await deps.users.delete_user(line_user_id)
    if keys is None:
        log.info("pdpa_user_not_found", user_ref=ref)
        return PdpaResult(user_found=False)

    deleted = 0
    failed: list[str] = []
    for key in keys:
        try:
            await deps.storage.delete(key)
        except Exception:
            # storage_key ไม่มีข้อมูลส่วนบุคคล (images/<conversation_id>/<line_message_id>) จึง log ได้
            log.exception("pdpa_file_delete_failed", user_ref=ref, storage_key=key)
            failed.append(key)
            continue
        deleted += 1
    log.info("pdpa_user_deleted", user_ref=ref, files_deleted=deleted, files_failed=len(failed))
    return PdpaResult(user_found=True, files_deleted=deleted, failed_keys=failed)


__all__ = ["PdpaResult", "delete_user_data", "user_ref"]

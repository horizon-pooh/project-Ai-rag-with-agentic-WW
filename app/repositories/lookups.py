"""app/repositories/lookups.py

ค้นข้อมูลเล็กๆ ที่ worker/api ต้องใช้แต่ไม่อยู่ในพอร์ตของ pipeline (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - line_user_id_for_conversation: cron handoff_timeout และ /close ต้อง push หาลูกค้า แต่ Conversation
   มีแค่ user_id และ UsersRepo ในพอร์ตไม่มีเมธอดหา user จาก id (ช่องว่างของสัญญางาน 2 — รายงานแล้ว)
 - user ที่ถูกลบตาม PDPA (deleted_at) -> None (ไม่ส่งข้อความ)

แนวคิดจาก: ใหม่ (งาน 8)
สถานะ: มีโค้ดแล้ว (งาน 8) — ควรย้ายเข้า UsersRepo ในพอร์ตเมื่อ orchestrator ตัดสิน
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


class PgLineUserLookup:
    """หา line_user_id ของเจ้าของ conversation"""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def line_user_id_for_conversation(self, conversation_id: int) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT u.line_user_id
                FROM conversations AS c
                JOIN line_users AS u ON u.id = c.user_id
                WHERE c.id = %s AND u.deleted_at IS NULL
                """,
                (conversation_id,),
            )
            row = await cur.fetchone()
        return None if row is None else row["line_user_id"]


__all__ = ["PgLineUserLookup"]

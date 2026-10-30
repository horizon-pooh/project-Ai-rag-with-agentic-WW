"""app/repositories/__init__.py

ชั้น repositories: ที่เดียวที่เขียน SQL

รายละเอียด:
 - SQL ตรง (psycopg 3 หรือ asyncpg) ไม่ใช้ ORM หนัก
 - เปลี่ยน schema แก้ที่นี่ที่เดียว (migrations/ เป็นต้นทาง)
 - transaction สั้น ไม่ถือค้างระหว่างรอ AI

แนวคิดจาก: src/store/ (แบบเก่า ใช้ Redis เก็บทุกอย่าง ล้าสมัย)
สถานะ: มีโค้ดแล้ว (งาน 7) — psycopg 3 async + psycopg_pool; Repos/make_repos รวม repository ทุกตัวบน pool เดียว
 - PgReplyTokens (reply_tokens.py, D-010) ไม่อยู่ใน Repos: ต้องใช้ REPLY_TOKEN_TTL_S -> worker/deps.build_deps สร้างเอง
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from app.repositories.ai_runs import PgAiRunsRepo
from app.repositories.conversations import PgConversationsRepo
from app.repositories.db import close_pool, open_pool
from app.repositories.feedback import PgFeedbackRepo
from app.repositories.flags import PgFlagsRepo
from app.repositories.messages import PgMessagesRepo
from app.repositories.raw_events import PgRawEventsRepo
from app.repositories.reply_tokens import PgReplyTokens
from app.repositories.state import PgStateRepo
from app.repositories.users import PgUsersRepo


@dataclass(frozen=True, kw_only=True)
class Repos:
    """repository ทุกตัว (ชื่อฟิลด์ตรงกับฟิลด์ของ Deps ใน app/pipeline/ports.py)"""

    raw_events: PgRawEventsRepo
    users: PgUsersRepo
    conversations: PgConversationsRepo
    state: PgStateRepo
    messages: PgMessagesRepo
    ai_runs: PgAiRunsRepo
    feedback: PgFeedbackRepo
    flags: PgFlagsRepo


def make_repos(pool: AsyncConnectionPool) -> Repos:
    """สร้าง repository ทุกตัวบน pool เดียวกัน (pool เปิดด้วย open_pool แล้ว)"""
    return Repos(
        raw_events=PgRawEventsRepo(pool),
        users=PgUsersRepo(pool),
        conversations=PgConversationsRepo(pool),
        state=PgStateRepo(pool),
        messages=PgMessagesRepo(pool),
        ai_runs=PgAiRunsRepo(pool),
        feedback=PgFeedbackRepo(pool),
        flags=PgFlagsRepo(pool),
    )


__all__ = [
    "PgAiRunsRepo",
    "PgConversationsRepo",
    "PgFeedbackRepo",
    "PgFlagsRepo",
    "PgMessagesRepo",
    "PgRawEventsRepo",
    "PgReplyTokens",
    "PgStateRepo",
    "PgUsersRepo",
    "Repos",
    "close_pool",
    "make_repos",
    "open_pool",
]

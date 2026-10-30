"""app/worker/admin.py

คำสั่งผู้ดูแลแบบรันครั้งเดียว (ไม่ใช่งานในคิว) — ใน docker compose: `docker compose exec worker python -m app.worker.admin ...`

รายละเอียด:
 - `pdpa-delete <line_user_id>`: ลบข้อมูลของลูกค้าตาม PDPA ผ่าน app/pipeline/pdpa.delete_user_data
   (DB transaction เดียว แล้วลบไฟล์ใน MinIO)
 - `kill-switch on|off|status`: เปิด/ปิด ops_flags['kill_switch'] ผ่าน services/kill_switch.set_kill_switch
   แล้วพิมพ์สถานะ env (KILL_SWITCH) / DB / ผลรวม (env=true บังคับเปิด ปิดด้วยคำสั่งนี้ไม่ได้)
 - `close-link <conversation_id>`: ทางชั่วคราว — ช่องทางแจ้ง call center ยังรอคำตอบ และ LogNotifier ไม่ log ลิงก์
   (token เป็นความลับ) จึงให้ผู้ดูแลสร้างลิงก์ /close ใหม่ของรอบส่งต่อปัจจุบัน (ConversationsRepo.create_close_token
   ด้วย handoff_started_at ของแถว + CLOSE_TOKEN_TTL_HOURS) แล้วพิมพ์ `{PUBLIC_BASE_URL}/close?token=...`
   **ลิงก์ที่พิมพ์เป็นความลับ** ใครมีลิงก์ปิดเคสได้ ห้ามแปะในที่สาธารณะ/ticket/log; ไม่ใช่ human หรือไม่ได้ตั้ง
   PUBLIC_BASE_URL -> ไม่สร้าง token พิมพ์เหตุผลแทน
 - `conversations --status human [--limit N]`: รายการเคสที่อยู่กับพนักงาน (ส่งต่อล่าสุดก่อน) ไว้หา conversation_id
 - ประกอบ Deps ด้วย worker/deps.build_deps (อ่าน env เหมือน worker) ปิดทุกอย่างเมื่อจบ
 - ทุกคำสั่งผ่าน ports/repositories (ไม่มี SQL ที่นี่)
 - พิมพ์แค่ id ภายใน/จำนวน (และ user_ref = hash ตัดสั้น) ไม่พิมพ์/ไม่ log line_user_id ตัวจริง
 - exit code: 0 = เสร็จ, 1 = ทำไม่ครบ/ทำไม่ได้ (pdpa-delete: ลบใน DB แล้วแต่ลบไฟล์บางไฟล์ไม่ได้ ดู log
   pdpa_file_delete_failed แล้วลบซ้ำเอง; close-link: ไม่มีเคส / ไม่ใช่ human / ไม่ได้ตั้ง PUBLIC_BASE_URL),
   2 = argument ผิด (argparse)
 - Windows: psycopg async ต้องใช้ SelectorEventLoop (production เป็น Linux container)

แนวคิดจาก: ใหม่ (R2-A)
สถานะ: มีโค้ดแล้ว (R2-A + คำสั่งสำหรับทดสอบบนเครื่องจริง ดู docs/test-deploy.md)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.logging import configure_logging
from app.pipeline import handoff, pdpa
from app.pipeline.ports import Deps
from app.pipeline.schemas import Conversation, ConversationStatus
from app.services.kill_switch import FLAG_KEY, FlagsKillSwitch, set_kill_switch
from app.worker.deps import build_deps, close_resources

EXIT_OK = 0
EXIT_FILES_FAILED = 1
# close-link ทำไม่ได้ (พิมพ์เหตุผลแล้ว)
EXIT_REFUSED = 1

KILL_SWITCH_ACTIONS = ("on", "off", "status")
CONVERSATION_STATUSES = (ConversationStatus.HUMAN.value,)
DEFAULT_LIST_LIMIT = 50

# เหตุผลที่ close-link ไม่สร้างลิงก์
CLOSE_NOT_FOUND = "not_found"
CLOSE_NOT_HUMAN = "not_human"
CLOSE_NO_BASE_URL = "no_public_base_url"
_CLOSE_REASON_TEXT = {
    CLOSE_NOT_FOUND: "ไม่มี conversation นี้",
    CLOSE_NOT_HUMAN: "conversation นี้ไม่ได้อยู่กับพนักงาน (status ไม่ใช่ human) ไม่ต้องปิด",
    CLOSE_NO_BASE_URL: "ไม่ได้ตั้ง PUBLIC_BASE_URL ใน .env สร้างลิงก์ไม่ได้",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.worker.admin", description="คำสั่งผู้ดูแล")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pdpa-delete", help="ลบข้อมูลลูกค้าหนึ่งคนตาม PDPA (ย้อนกลับไม่ได้)")
    p.add_argument("line_user_id", help="LINE user id (Uxxxxxxxx...)")
    k = sub.add_parser("kill-switch", help="เปิด/ปิด/ดู kill switch (ops_flags ใน DB)")
    k.add_argument("action", choices=KILL_SWITCH_ACTIONS)
    c = sub.add_parser("close-link", help="สร้างลิงก์ /close ใหม่ของเคสที่อยู่กับพนักงาน (ลิงก์เป็นความลับ)")
    c.add_argument("conversation_id", type=int)
    ls = sub.add_parser("conversations", help="รายการ conversation ตาม status")
    ls.add_argument("--status", choices=CONVERSATION_STATUSES, required=True)
    ls.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """แยก argument; line_user_id ว่าง (หลังตัดช่องว่าง) / --limit < 1 -> error ของ argparse (exit 2)"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "pdpa-delete":
        args.line_user_id = args.line_user_id.strip()
        if not args.line_user_id:
            parser.error("line_user_id ว่าง")
    if args.command == "conversations" and args.limit < 1:
        parser.error("--limit ต้องมากกว่า 0")
    return args


async def run_with_deps[T](settings: Settings, fn: Callable[[Deps], Awaitable[T]]) -> T:
    """ประกอบ Deps ตัวจริง รัน fn แล้วปิดทุกอย่าง"""
    deps, res = await build_deps(settings)
    try:
        return await fn(deps)
    finally:
        await close_resources(res)


async def run_pdpa_delete(settings: Settings, line_user_id: str) -> pdpa.PdpaResult:
    return await run_with_deps(settings, lambda deps: pdpa.delete_user_data(deps, line_user_id))


def format_result(line_user_id: str, result: pdpa.PdpaResult) -> str:
    """บรรทัดผลที่พิมพ์ (ไม่มี line_user_id ตัวจริง)"""
    return (
        f"pdpa-delete user_ref={pdpa.user_ref(line_user_id)} "
        f"user_found={'yes' if result.user_found else 'no'} "
        f"files_deleted={result.files_deleted} files_failed={len(result.failed_keys)}"
    )


# --- kill-switch ---


@dataclass(frozen=True)
class KillSwitchStatus:
    env_on: bool
    # ค่าดิบใน ops_flags (None = ยังไม่เคยตั้ง)
    db_value: str | None
    effective_on: bool


async def kill_switch(deps: Deps, action: str) -> KillSwitchStatus:
    """on/off -> set_kill_switch ก่อน; ทุก action คืนสถานะหลังทำ"""
    if action not in KILL_SWITCH_ACTIONS:
        raise ValueError(f"action ไม่รู้จัก: {action}")
    if action != "status":
        await set_kill_switch(deps.flags, action == "on")
    return KillSwitchStatus(
        env_on=deps.settings.kill_switch,
        db_value=await deps.flags.get_flag(FLAG_KEY),
        effective_on=await FlagsKillSwitch(deps.settings, deps.flags).is_on(),
    )


def format_kill_switch(status: KillSwitchStatus) -> str:
    line = (
        f"kill-switch effective={'on' if status.effective_on else 'off'} "
        f"env={'on' if status.env_on else 'off'} db={status.db_value or 'unset'}"
    )
    if status.env_on:
        line += " (KILL_SWITCH=true ใน .env บังคับเปิด: แก้ .env แล้วสร้าง api/worker ใหม่)"
    return line


# --- close-link ---


@dataclass(frozen=True)
class CloseLinkResult:
    # ลิงก์มี token จริง — เป็นความลับ พิมพ์ให้ผู้ดูแลเท่านั้น ห้าม log
    url: str | None = None
    reason: str | None = None


async def close_link(deps: Deps, conversation_id: int) -> CloseLinkResult:
    """สร้าง token ใหม่ผูกกับ handoff_started_at ปัจจุบัน (ลิงก์ของรอบนี้ ปิดรอบถัดไปไม่ได้)"""
    conv = await deps.conversations.get(conversation_id)
    if conv is None:
        return CloseLinkResult(reason=CLOSE_NOT_FOUND)
    if conv.status is not ConversationStatus.HUMAN or conv.handoff_started_at is None:
        return CloseLinkResult(reason=CLOSE_NOT_HUMAN)
    if not deps.settings.public_base_url:
        return CloseLinkResult(reason=CLOSE_NO_BASE_URL)
    token = await deps.conversations.create_close_token(
        conv.id, conv.handoff_started_at, deps.settings.close_token_ttl
    )
    return CloseLinkResult(url=handoff.close_url_for(deps.settings, token))


def format_close_link(conversation_id: int, result: CloseLinkResult) -> str:
    if result.url is not None:
        return result.url
    text = _CLOSE_REASON_TEXT.get(result.reason or "", result.reason or "-")
    return f"close-link conversation_id={conversation_id} ไม่สร้างลิงก์: {text}"


# --- conversations ---


async def list_conversations(deps: Deps, status: str, limit: int) -> list[Conversation]:
    if status != ConversationStatus.HUMAN.value:
        raise ValueError(f"status ที่รองรับ: {CONVERSATION_STATUSES}")
    return await deps.conversations.list_open_human(limit)


def format_conversation(conv: Conversation) -> str:
    """หนึ่งบรรทัดต่อเคส — id ภายในเท่านั้น (ไม่มี line_user_id / ข้อความลูกค้า)"""
    started = conv.handoff_started_at.isoformat() if conv.handoff_started_at else "-"
    return (
        f"conversation_id={conv.id} user_id={conv.user_id} status={conv.status.value} "
        f"handoff_started_at={started} handoff_reason={conv.handoff_reason or '-'}"
    )


# --- main ---


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=loop_factory)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    if args.command == "kill-switch":
        status = _run(run_with_deps(settings, lambda deps: kill_switch(deps, args.action)))
        print(format_kill_switch(status))
        return EXIT_OK

    if args.command == "close-link":
        link = _run(run_with_deps(settings, lambda deps: close_link(deps, args.conversation_id)))
        print(format_close_link(args.conversation_id, link))
        return EXIT_OK if link.url is not None else EXIT_REFUSED

    if args.command == "conversations":
        convs = _run(
            run_with_deps(settings, lambda deps: list_conversations(deps, args.status, args.limit))
        )
        for conv in convs:
            print(format_conversation(conv))
        print(f"conversations status={args.status} count={len(convs)}")
        return EXIT_OK

    result = _run(run_pdpa_delete(settings, args.line_user_id))
    print(format_result(args.line_user_id, result))
    return EXIT_FILES_FAILED if result.failed_keys else EXIT_OK


__all__ = [
    "CloseLinkResult",
    "KillSwitchStatus",
    "build_parser",
    "close_link",
    "format_close_link",
    "format_conversation",
    "format_kill_switch",
    "format_result",
    "kill_switch",
    "list_conversations",
    "main",
    "parse_args",
    "run_pdpa_delete",
    "run_with_deps",
]


if __name__ == "__main__":
    raise SystemExit(main())

"""app/worker/admin.py

คำสั่งผู้ดูแลแบบรันครั้งเดียว (ไม่ใช่งานในคิว)

รายละเอียด:
 - `python -m app.worker.admin pdpa-delete <line_user_id>`: ลบข้อมูลของลูกค้าตาม PDPA ผ่าน
   app/pipeline/pdpa.delete_user_data (DB transaction เดียว แล้วลบไฟล์ใน MinIO)
 - ประกอบ Deps ด้วย worker/deps.build_deps (อ่าน env เหมือน worker) ปิดทุกอย่างเมื่อจบ
 - พิมพ์แค่จำนวน (และ user_ref = hash ตัดสั้น) ไม่พิมพ์/ไม่ log line_user_id ตัวจริง
 - exit code: 0 = เสร็จ (รวมกรณีไม่มี user — ลบซ้ำได้), 1 = ลบใน DB แล้วแต่ลบไฟล์บางไฟล์ไม่ได้ (ดู log
   pdpa_file_delete_failed แล้วลบซ้ำเอง), 2 = argument ผิด (argparse)
 - Windows: psycopg async ต้องใช้ SelectorEventLoop (production เป็น Linux container)

แนวคิดจาก: ใหม่ (R2-A)
สถานะ: มีโค้ดแล้ว (R2-A)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.config import Settings, get_settings
from app.logging import configure_logging
from app.pipeline import pdpa
from app.worker.deps import build_deps, close_resources

EXIT_OK = 0
EXIT_FILES_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.worker.admin", description="คำสั่งผู้ดูแล")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pdpa-delete", help="ลบข้อมูลลูกค้าหนึ่งคนตาม PDPA (ย้อนกลับไม่ได้)")
    p.add_argument("line_user_id", help="LINE user id (Uxxxxxxxx...)")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """แยก argument; line_user_id ว่าง (หลังตัดช่องว่าง) -> error ของ argparse (exit 2)"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "pdpa-delete":
        args.line_user_id = args.line_user_id.strip()
        if not args.line_user_id:
            parser.error("line_user_id ว่าง")
    return args


async def run_pdpa_delete(settings: Settings, line_user_id: str) -> pdpa.PdpaResult:
    deps, res = await build_deps(settings)
    try:
        return await pdpa.delete_user_data(deps, line_user_id)
    finally:
        await close_resources(res)


def format_result(line_user_id: str, result: pdpa.PdpaResult) -> str:
    """บรรทัดผลที่พิมพ์ (ไม่มี line_user_id ตัวจริง)"""
    return (
        f"pdpa-delete user_ref={pdpa.user_ref(line_user_id)} "
        f"user_found={'yes' if result.user_found else 'no'} "
        f"files_deleted={result.files_deleted} files_failed={len(result.failed_keys)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    result = asyncio.run(run_pdpa_delete(settings, args.line_user_id), loop_factory=loop_factory)
    print(format_result(args.line_user_id, result))
    return EXIT_FILES_FAILED if result.failed_keys else EXIT_OK


__all__ = ["build_parser", "format_result", "main", "parse_args", "run_pdpa_delete"]


if __name__ == "__main__":
    raise SystemExit(main())

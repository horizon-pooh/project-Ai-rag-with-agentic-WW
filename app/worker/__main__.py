"""app/worker/__main__.py

`python -m app.worker` — เริ่ม worker ของคิว procrastinate (คำสั่งของ service worker ใน docker-compose.yml)

รายละเอียด:
 - อ่าน env ผ่าน get_settings (ขาดค่าบังคับ = หยุดทันที) แล้วรัน worker/app.run_worker จนได้ SIGTERM/SIGINT
 - ใช้ทางรันในโค้ด (App.run_worker_async ขณะ app เปิดอยู่ ตามเอกสาร procrastinate howto/basics/worker)
   แทน CLI `procrastinate worker` เพราะต้องประกอบ Deps / PipelineConfig ก่อนเริ่มและปิดเมื่อจบ
 - Windows: psycopg async ต้องใช้ SelectorEventLoop (production เป็น Linux container)

แนวคิดจาก: ใหม่ (D-010 แทน `arq app.worker.settings.WorkerSettings`)
สถานะ: มีโค้ดแล้ว (D-010)
"""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.worker.app import run_worker


def main() -> None:
    settings = get_settings()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run_worker(settings), loop_factory=loop_factory)


if __name__ == "__main__":
    main()

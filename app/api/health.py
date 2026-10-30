"""app/api/health.py

GET /health: บอกว่า api ยังอยู่ และต่อ Postgres ได้

รายละเอียด:
 - ใช้กับ Uptime Kuma และ healthcheck ของ docker compose
 - ไม่เปิดเผยค่า config หรือ secret
 - แยก liveness (process อยู่) กับ readiness (ต่อ DB ได้)

ข้อตัดสินในงาน 8:
 - GET /health = liveness ไม่แตะ DB; GET /health/ready = Postgres SELECT 1 -> 200 / 503
   (D-010: ไม่มี Redis แล้ว คิวก็อยู่ใน Postgres ตัวเดียวกัน)
 - ผลบอกแค่ชื่อการตรวจกับ true/false ไม่บอกข้อความ error (อาจมี host/รหัสผ่าน)
 - การตรวจจริงอยู่ที่ worker/deps.check_ready (SQL อยู่ใน repositories/health.py) ตั้งไว้ที่ app.state.ready_check

แนวคิดจาก: src/routes/health.js (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"}, headers=_NO_STORE)


@router.api_route("/health/ready", methods=["GET", "HEAD"])
async def ready(request: Request) -> JSONResponse:
    check = getattr(request.app.state, "ready_check", None)
    checks: dict[str, bool] = {}
    if check is not None:
        try:
            checks = {str(k): bool(v) for k, v in (await check()).items()}
        except Exception:
            checks = {}
    ok = bool(checks) and all(checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status_code=200 if ok else 503,
        headers=_NO_STORE,
    )


__all__ = ["router"]

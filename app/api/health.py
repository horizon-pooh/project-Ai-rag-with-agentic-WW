"""app/api/health.py

GET /health: บอกว่า api ยังอยู่ และต่อ Postgres / Redis ได้

รายละเอียด:
 - ใช้กับ Uptime Kuma และ healthcheck ของ docker compose
 - ไม่เปิดเผยค่า config หรือ secret
 - แยก liveness (process อยู่) กับ readiness (ต่อ DB/Redis ได้)

แนวคิดจาก: src/routes/health.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

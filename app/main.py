"""app/main.py

สร้าง FastAPI app (app factory) และต่อ router ทั้งหมด ไม่มี business logic

รายละเอียด:
 - create_app(): โหลด config ครั้งเดียว, ตั้ง logging, ต่อ Sentry (ถ้ามี SENTRY_DSN)
 - ต่อ router: api/webhook, api/health, api/close, api/fast_path
 - เปิด/ปิด connection pool ของ Postgres และ Redis ตอน startup/shutdown
 - ห้ามมี dev routes ใน production
 - รันด้วย uvicorn (คำสั่งอยู่ใน docker-compose.yml)

แนวคิดจาก: src/app.js, src/server.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

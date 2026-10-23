"""app/worker/__init__.py

ชั้น worker: process แยกที่หยิบงานจากคิว procrastinate (คิวบน Postgres, D-010) มารัน pipeline

รายละเอียด:
 - Worker ไม่เก็บสถานะในหน่วยความจำ ความจริงอยู่ที่ Postgres (restart ได้ทุกเมื่อ)
 - เริ่มด้วย `python -m app.worker` (__main__.py)

แนวคิดจาก: src/jobs/ (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8 + D-010) — ดู app (คิว/งาน periodic/ตัวรัน) / tasks / cron / deps / admin
"""

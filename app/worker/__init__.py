"""app/worker/__init__.py

ชั้น worker: process แยกที่หยิบงานจากคิว arq มารัน pipeline

รายละเอียด:
 - Worker ไม่เก็บสถานะในหน่วยความจำ ความจริงอยู่ที่ Postgres (restart ได้ทุกเมื่อ)

แนวคิดจาก: src/jobs/ (แบบเก่า)
สถานะ: มีโค้ดแล้ว (งาน 8) — ดู tasks/locks/cron/settings/deps
"""

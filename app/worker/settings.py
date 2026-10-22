"""app/worker/settings.py

WorkerSettings ของ arq: ฟังก์ชันงาน, cron, การเชื่อมต่อ Redis

รายละเอียด:
 - functions = [tasks.process_event]
 - cron_jobs = [requeue_pending, handoff_timeout, retention] (ดู cron.py)
 - on_startup / on_shutdown: เปิด-ปิด pool ของ Postgres, httpx client, MinIO
 - max_jobs สัมพันธ์กับ AI_CONCURRENCY (ค่าจริงรอคำตอบเรื่อง AI API)
 - job_timeout > TURN_DEADLINE_S
 - Redis เป็นคิวอย่างเดียว

แนวคิดจาก: src/jobs/softnixWorker.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

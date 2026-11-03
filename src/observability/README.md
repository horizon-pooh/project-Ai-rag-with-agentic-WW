# src/observability/

log และตัวเลข สำหรับไล่ปัญหาย้อนหลัง

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `logger.js` | log มีเวลา + รหัส evt-xxxxxx ร้อยข้อความเดียวกัน | `line_/logger.js` |
| `redact.js` | ซ่อนข้อมูลส่วนตัวใน log (เบอร์โทร, อีเมล, ชื่อ) ตาม PDPA | ใหม่ |
| `metrics.js` | ตัวเลขเวลาต่อข้อความ (รอคิว / Softnix / ส่ง) | `line_/turnMetrics.js` |

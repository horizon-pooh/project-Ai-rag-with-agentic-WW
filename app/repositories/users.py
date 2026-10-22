"""app/repositories/users.py

ตาราง line_users และ line_consents (ที่เดียวที่เขียน SQL)

รายละเอียด:
 - หา/สร้าง user จาก LINE user id
 - consent: อ่าน/บันทึกการยินยอม (PDPA)
 - ลบตาม PDPA ทำผ่าน line_users ที่เดียว (cascade) ไฟล์ใน MinIO ต้องลบแยก

แนวคิดจาก: src/store/redisStore.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

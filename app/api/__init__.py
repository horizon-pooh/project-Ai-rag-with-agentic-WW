"""app/api/__init__.py

ชั้น api: route ของ FastAPI รับ HTTP แล้วส่งต่อ ไม่มี business logic

รายละเอียด:
 - ไม่เรียก AI ไม่รัน pipeline (งานหนักอยู่ใน worker)
 - เขียน DB ผ่าน repositories เท่านั้น

แนวคิดจาก: src/routes/ (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

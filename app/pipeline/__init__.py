"""app/pipeline/__init__.py

ชั้น pipeline: ขั้นตอนต่อหนึ่งข้อความ (prepare -> ... -> send)

รายละเอียด:
 - pipeline ไม่เขียน SQL และไม่เรียก LINE เอง รับตัวช่วยเข้ามาจากข้างนอก
 - หนึ่ง step หนึ่งหน้าที่ รับ/คืนชนิดข้อมูลใน schemas.py

แนวคิดจาก: src/pipeline/, src/conversation/ (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

"""app/repositories/__init__.py

ชั้น repositories: ที่เดียวที่เขียน SQL

รายละเอียด:
 - SQL ตรง (psycopg 3 หรือ asyncpg) ไม่ใช้ ORM หนัก
 - เปลี่ยน schema แก้ที่นี่ที่เดียว (migrations/ เป็นต้นทาง)
 - transaction สั้น ไม่ถือค้างระหว่างรอ AI

แนวคิดจาก: src/store/ (แบบเก่า ใช้ Redis เก็บทุกอย่าง ล้าสมัย)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

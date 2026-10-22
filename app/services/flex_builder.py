"""app/services/flex_builder.py

สร้างข้อความ LINE จากข้อมูลดิบ (โค้ดสร้าง ไม่ใช่ AI)

รายละเอียด:
 - product carousel: การ์ดสินค้าไม่เกิน 10 ใบ ราคา/ลิงก์/SKU มาจาก products ของ API เท่านั้น
 - ปุ่ม 'ดูรายละเอียด' เป็น postback ที่มี SKU
 - quick reply: คุยกับพนักงาน, เริ่มใหม่ ฯลฯ
 - text templates: กลับมาคุยกับ AI แล้ว, กำลังคุยกับ AI อยู่, หมดเวลาคุยกับพนักงานแล้ว, ข้อมูลก่อนหน้าหมดอายุ, ขอโทษระบบขัดข้อง, กำลังค้นหา
 - เคารพขีดจำกัดของ LINE (validate ตรวจซ้ำ)

แนวคิดจาก: src/line/messages/productCarousel.js, quickReply.js, textMessages.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

"""app/pipeline/steps/update_state.py

ขั้น 7: อัปเดต conversation_state หลังได้ AiResult

รายละเอียด:
 - API คืน state_out -> ใช้ state_out
 - ไม่คืน -> วิธีสำรอง (เช่นส่งแชทย้อนหลังแทน Delta) รอคำตอบเรื่อง AI API ข้อ 3
 - ต่ออายุ state 2 ชม. (STATE_TTL_HOURS) ทุกครั้งที่ user พิมพ์ในโหมด bot
 - state เก็บหมวด, filter, SKU ล่าสุด; ไม่เก็บรูป
 - transaction สั้นเฉพาะตอนอ่านแล้วเขียน

แนวคิดจาก: ใหม่ (ไม่มีในแบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

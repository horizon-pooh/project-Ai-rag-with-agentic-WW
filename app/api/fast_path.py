"""app/api/fast_path.py

ทางด่วนของคำสั่งเปลี่ยนสถานะ ไม่รอล็อกต่อ user (เสนอ)

รายละเอียด:
 - ปัญหา: postback talk_to_staff ที่มาระหว่างรอ AI ต้องรอล็อก -> AI ตอบก่อน แล้วค่อยส่งต่อ (ช้าเป็นนาทีถ้า API ช้า)
 - คำสั่งที่วิ่งทางนี้: postback action=talk_to_staff, action=resume_ai, คำขอคนชัดเจนตามกฎขั้น 5
 - talk_to_staff: start_handoff ด้วย UPDATE ... WHERE status='bot' (ใครมาก่อนชนะ)
 - พอสถานะเป็น human แล้ว sendGuard จะทิ้งคำตอบ AI ที่มาช้า (= เคส escalationRace เดิม)
 - event เดิมยังเข้าคิวตามปกติ; handoff_check ต้องเห็นว่าทำไปแล้วและไม่ทำซ้ำ
 - ยังไม่ตัดสิน: เรียกจาก webhook ตรงๆ หรือคิวแยกที่ไม่ล็อก

แนวคิดจาก: src/conversation/handlePostback.js, docs/diagrams/03-escalation-race.html (แนวคิดเดิม), จุดเรียกใหม่
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

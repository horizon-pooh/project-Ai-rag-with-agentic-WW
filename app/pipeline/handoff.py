"""app/pipeline/handoff.py

start_handoff / end_handoff: จุดเดียวที่เปลี่ยนสถานะ bot <-> human

รายละเอียด:
 - start_handoff(conv, reason): UPDATE ... SET status='human', handoff_started_at=now() WHERE status='bot'; สำเร็จ -> แจ้ง call center พร้อมลิงก์ /close
 - end_handoff(conv, reason): reason = user_resumed / timeout / staff_closed; ใช้ร่วม 3 ทาง: handoff_check, cron handoff_timeout, api/close
 - UPDATE แบบมีเงื่อนไข: UPDATE conversations SET status='bot', handoff_ended_at=now(), handoff_end_reason=$2 WHERE id=$1 AND status='human' AND handoff_started_at=$3 RETURNING *
 - ใครมาก่อนชนะ: ไม่ได้แถวคืน -> ไม่ทำอะไร (ไม่ส่งข้อความ/ไม่แจ้งพนักงานซ้ำ)
 - สำเร็จ -> ล้าง conversation_state, แจ้งพนักงานว่าลูกค้ากลับไปคุยกับ AI
 - SQL จริงอยู่ใน repositories/conversations.py ที่นี่เรียกผ่านตัวช่วย

แนวคิดจาก: src/conversation/escalate.js, src/conversation/stateMachine.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

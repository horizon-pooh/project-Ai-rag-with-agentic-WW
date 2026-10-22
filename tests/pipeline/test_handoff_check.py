"""tests/pipeline/test_handoff_check.py

เคสทดสอบของ app/pipeline/steps/handoff_check.py (ยังไม่มีฟังก์ชันทดสอบ)

รายละเอียด (สถานะ / ข้อความเข้า -> ผลที่คาด):
 - human, เกิน 6 ชม., ข้อความทั่วไป -> end_handoff(timeout) -> CONTINUE + timed_out
 - human, postback action=resume_ai -> end_handoff(user_resumed) -> RESUMED
 - human, พิมพ์ "คุยกับ AI" / " คุย กับ ai " / "คุยกับAI" (มี zero-width) -> RESUMED
 - human, พิมพ์ "คุยกับ AI หน่อย หาตู้เย็น" -> SILENT (ไม่นับเป็นคำสั่ง ตั้งใจ)
 - bot, postback action=resume_ai หรือพิมพ์ "คุยกับ AI" -> ALREADY_BOT
 - bot, postback action=talk_to_staff -> ESCALATE
 - human, postback action=talk_to_staff -> SILENT
 - human, ข้อความทั่วไป / รูป -> SILENT (บันทึกแต่ไม่ตอบ, รูปไม่ส่ง AI)
 - bot, ข้อความทั่วไป -> CONTINUE
 - human, เกิน 6 ชม. + พิมพ์ "คุยกับ AI" -> timeout ชนะก่อน แล้ว ALREADY_BOT (ไม่ส่งข้อความซ้ำ)
 - end_handoff แข่งกัน (cron timeout กับลูกค้ากดคืน / /close) -> UPDATE มีเงื่อนไข สำเร็จทางเดียว แจ้งพนักงานครั้งเดียว
 - end_handoff สำเร็จ -> conversation_state ถูกล้าง
 - ข้อความระหว่าง human ไม่ต่ออายุ state และไม่เลื่อน handoff_started_at
 - fast_path ส่งต่อไปแล้ว แล้ว event เดิมมาถึง -> ไม่ส่งต่อซ้ำ

แนวคิดจาก: test/unit/README.md (แบบเก่า), เคสใหม่จาก docs/Line_ai_bot.md หัวข้อ การส่งต่อพนักงานและการคืน AI
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

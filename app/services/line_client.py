"""app/services/line_client.py

client ของ LINE Messaging API: ที่เดียวที่ถือ channel access token

รายละเอียด:
 - reply(reply_token, messages), push(user_id, messages)
 - show_loading(user_id, seconds): loading animation ระหว่างรอ AI
 - ดึงเนื้อหารูปจาก LINE (มี timeout)
 - ใช้ line-bot-sdk หรือ httpx async
 - token ห้ามส่งให้ AI API ห้ามลง log
 - แยก error ที่ retry ได้ (5xx, timeout) กับไม่ได้ (4xx, replyToken หมดอายุ)
 - pipeline เรียกผ่าน steps/send เท่านั้น

แนวคิดจาก: src/line/lineApi.js (แบบเก่า)
สถานะ: ยังไม่มีโค้ด (โครงสร้างเท่านั้น)
"""

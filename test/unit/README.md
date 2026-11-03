# test/unit/

เทสต์ทีละไฟล์ ไม่ต้องเปิด server

## ย้ายจาก MVP

- `line_/test/unit/stateMachine.test.js`
- `line_/test/unit/sessionStore.contract.test.js` + `storeContract.js` (ใช้เทสต์ทั้ง memory และ redis)
- `line_/test/unit/dedupe.test.js`, `userQueue.test.js`, `lineClient.test.js`, `decisionRules.test.js`

## ของใหม่

- `sendGuard.test.js` — ESCALATED แล้วต้องไม่ส่งข้อความจาก AI
- `softnixQueue.test.js` — คิวเต็ม, ยกเลิก, งานใหม่แทนงานเก่า
- `killSwitch.test.js`

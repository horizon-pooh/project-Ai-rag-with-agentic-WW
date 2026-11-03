# src/store/

ที่เก็บข้อมูล: memory สำหรับ dev, Redis สำหรับ production

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `index.js` | interface กลางของที่เก็บข้อมูล เลือกด้วย STORE=memory|redis | `line_/sessionStore.js` |
| `memoryStore.js` | เก็บใน Map ใช้ตอน dev/test เท่านั้น | `line_/sessionStore.js` |
| `redisStore.js` | เก็บใน Redis ใช้ตอน production | ใหม่ |

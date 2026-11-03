# src/

โค้ดที่รันจริงบน production ทั้งหมด

| ไฟล์ | หน้าที่ | มาจาก MVP |
|---|---|---|
| `server.js` | จุดเริ่มโปรแกรม: โหลด config, ต่อ Redis, เปิด HTTP server, เริ่ม worker และปิดอย่างปลอดภัย | `line_/server.js` |
| `app.js` | ประกอบ Express: ผูก routes กับ middleware ไม่มี logic ธุรกิจ | `line_/server.js` |

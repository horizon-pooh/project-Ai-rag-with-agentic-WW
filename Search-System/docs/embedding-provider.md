# โมเดลสร้างเวกเตอร์

ใช้ Qwen3-Embedding-0.6B แบบ GGUF Q8_0 ผ่าน llama.cpp บน BPLUTONIX ไม่เรียกบริการ embedding ภายนอก

ค่าที่ใช้: เวกเตอร์ 1,024 มิติ, context 2,048 token, pooling `last`, normalization `server_l2` และ llama.cpp รุ่น `b11753-d8d92973c`

## เปิดใช้งาน

ถ้าโมเดลรันอยู่ให้ใช้ process เดิม มิฉะนั้นเปิดจาก Mac แล้วค้าง terminal ไว้:

```bash
npm run embedding:serve:remote
```

เปิด tunnel อีก terminal แล้วตรวจความพร้อม:

```bash
npm run embedding:tunnel
curl --fail --max-time 5 http://127.0.0.1:18081/health
npm run embedding:check -- --live
```

ครั้งแรกต้องมี launcher บนเซิร์ฟเวอร์ ใช้คำสั่งนี้ส่งไฟล์:

```bash
scp scripts/start-embedding-server.sh antonius@100.116.20.59:~/ai/start-search-embedding.sh
```

หากรันบนเซิร์ฟเวอร์โดยตรงใช้ `npm run embedding:serve` และ URL `http://127.0.0.1:8081/v1` ส่วน Mac ใช้ `http://127.0.0.1:18081/v1` เปิดโมเดลและรอ health พร้อมก่อน Node ไม่เปิดโมเดลซ้ำทุกครั้งที่ Node เริ่ม

## การตรวจคำขอ

เก็บ profile ใน `.env.embedding.local` คำค้นใช้ prefix ตามที่กำหนด ส่วนเอกสารใช้ prefix ของเอกสาร ตรวจชื่อโมเดล build มิติ context และจำนวน token จริงก่อนสร้างเวกเตอร์

ไม่ตัดข้อความที่เกินขนาดเงียบ ๆ ผลต้องเป็นตัวเลขครบมิติ ไม่เป็นศูนย์ และ norm ใกล้ 1 รองรับ timeout, ยกเลิกงาน และ retry ตามขอบเขตที่ตั้งไว้ ข้อผิดพลาดไม่เก็บข้อความหรือรหัสลับจาก provider

`configuration_hash` บอกชุดการตั้งค่า ไม่ใช่ SHA256 ของไฟล์ GGUF ต้องตรวจไฟล์จริงก่อนลงทะเบียนตาม [คู่มือสร้างเวกเตอร์](embedding-backfill.md)

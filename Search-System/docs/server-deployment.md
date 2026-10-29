# ติดตั้ง Search Tool บน BPLUTONIX

ใช้บัญชี antonius รัน API แบบ user service ฐานและโมเดลอยู่เครื่องเดียวกัน มี release แยกตาม Git commit และไฟล์ config ส่วนตัวนอก Git ยังไม่มีการติดตั้ง service แทนผู้ใช้ในชุดส่งมอบนี้

## 1. เตรียมบน Mac

ใน Search-System หลังวางไฟล์และทดสอบแล้ว:

```bash
npm run server:configure
```

สร้าง deploy-private/api.json แบบ600 เก็บ token/profile เดิม เปลี่ยน DB เป็น127.0.0.1:5432 และโมเดลเป็น127.0.0.1:8081 ใช้ HOST=100.116.20.59 เฉพาะ Tailscale ไม่สร้างหรือเปลี่ยนข้อมูลในฐาน ไม่พิมพ์ credentials และไม่เขียนทับไฟล์เดิม

ตรวจโค้ดแล้ว commit/push ก่อนแพ็ก เพื่อให้ revision ตรงกับไฟล์ที่ส่ง:

```bash
npm run release:pack
task_revision="$(git rev-parse HEAD)"
```

ได้ deploy-private/releases/<revision>.tar.gz และ .sha256 มีเฉพาะ Search-System ที่ commit แล้ว ไม่รวม config, node_modules, dist หรือ reports

เตรียมโฟลเดอร์ปลายทาง:

```bash
ssh antonius@100.116.20.59 'umask 077; mkdir -p ~/search-tool/incoming ~/search-tool/config ~/search-tool/releases'
scp "deploy-private/releases/$task_revision.tar.gz" "deploy-private/releases/$task_revision.tar.gz.sha256" deploy/install-release.sh deploy/activate-release.sh antonius@100.116.20.59:~/search-tool/incoming/
scp deploy-private/api.json antonius@100.116.20.59:~/search-tool/config/api.upload.json
```

config upload ใช้สำหรับติดตั้งครั้งแรก ถ้ามี api.json แล้วให้ใช้ค่าเดิมและทบทวนการเปลี่ยน config แยก ไม่ส่งไฟล์นี้ให้ผู้เรียก API เพราะมีรหัสบัญชีค้นหา

## 2. ตรวจเครื่องเซิร์ฟเวอร์

```bash
ssh antonius@100.116.20.59
node --version
command -v npm git bash tar gzip sha256sum ss
```

ต้องเป็น Linux และ Node24 พร้อม npm/git และเครื่องมือข้างต้น ไม่ใช้ Node บน Mac แทนผลตรวจเครื่องนี้ PostgreSQL ต้องฟังเฉพาะ loopback และ Tailscale ต้องมี IP100.116.20.59 ก่อนเปิด API ตรวจพอร์ต3000ก่อน หากมีโปรแกรมอื่นใช้ให้เลือก PORT อื่นใน api.json และ URL ผู้เรียก ไม่หยุดโปรแกรมอื่นเพื่อยึดพอร์ต

สำหรับ config ครั้งแรกเมื่อยังไม่มี api.json:

```bash
chmod 700 ~/search-tool ~/search-tool/config
chmod 600 ~/search-tool/config/api.upload.json
test ! -e ~/search-tool/config/api.json && mv ~/search-tool/config/api.upload.json ~/search-tool/config/api.json
```

ถ้ามีไฟล์เดิมอยู่แล้วอย่าย้ายทับ ให้ตรวจของเดิมก่อน

## 3. ติดตั้ง release ใหม่

ยังอยู่ใน SSH บนเซิร์ฟเวอร์ วาง revision 40 ตัวที่ release:pack แสดง:

```bash
read -r -p 'Revision 40 ตัวจาก Mac: ' task_revision
bash ~/search-tool/incoming/install-release.sh "$task_revision"
```

ตรวจ checksum/commit ใน archive แตกไฟล์ใน releases/<revision> แล้ว npm ci → typecheck → build → test → prune dev dependencies ไม่แตะ current/service เดิม ถ้าโฟลเดอร์ release นี้มีอยู่แล้วจะหยุด ไม่ลบทิ้งหรือเขียนทับเพื่อให้ผ่าน

release นี้ prune dev dependencies แล้ว งาน migration/backfill ให้ใช้ checkout สำหรับผู้ดูแลแยกที่ติดตั้ง dev dependencies และใช้ config/role ของงานนั้น ไม่ใส่ credentials ผู้ดูแลใน API

ไม่มี migration ใน Commit27 อย่ารัน SQL ทั้งชุดซ้ำ หาก production plan ยังมี pending ให้กลับไปตรวจ migration ตามคู่มือเดิมด้วยบัญชีผู้ดูแล แยกจาก runtime

## 4. ใช้โมเดลเดิมและตรวจ startup

ถ้าโมเดลรันอยู่ให้ใช้ process เดิม ตรวจ SHA256 ของ GGUF และรุ่น llama.cpp ให้ตรงกับ profile ที่ลงทะเบียนไว้ด้วย โดยไม่พิมพ์ config ทั้งไฟล์ที่มี credentials แล้วตรวจ health:

```bash
curl --fail --max-time 5 http://127.0.0.1:8081/health
cd ~/search-tool/releases/"$task_revision"/Search-System
SEARCH_SERVER_CONFIG="$HOME/search-tool/config/api.json" npm run server:check
```

ต้องได้ server_startup_ready ตรวจสิทธิ์บัญชีฐาน profile ที่ลงทะเบียน และเวกเตอร์คำค้นหนึ่งรายการ ไม่ backfill ไม่เขียนฐาน ไม่รับรอง checksum ไฟล์โมเดลที่โหลดแทนผู้ดูแล

ถ้าโมเดลยังไม่รันให้เปิดด้วย npm run embedding:serve ในอีก terminal รอ health ก่อน อย่าเปิดซ้ำ ตัว API ที่รันอยู่จะ fallback เมื่อโมเดลขัดข้อง แต่การเริ่ม API ใหม่ในโหมด deploy นี้ต้องผ่าน startup gate ก่อน

## 5. ติดตั้ง user units และเปิด API

ยังอยู่ใน release ใหม่:

```bash
npm run server:units
mkdir -p ~/.config/systemd/user
install -m 600 deploy-private/systemd/search-tool-api.service deploy-private/systemd/search-embedding.service ~/.config/systemd/user/
systemd-analyze --user verify ~/.config/systemd/user/search-tool-api.service ~/.config/systemd/user/search-embedding.service
systemctl --user daemon-reload
bash ~/search-tool/incoming/activate-release.sh "$task_revision"
systemctl --user enable --now search-tool-api.service
```

unit ใช้ path Node ที่รัน server:units จริง หาก verify แสดงข้อผิดพลาดให้แก้บนเครื่องนี้ก่อนเปิด service ยังไม่ได้อ้างว่าผ่าน Linux/systemd จากการทดสอบบน Mac

API wrapper ตรวจฐานและรอโมเดลก่อน import server ไม่โหลด GPU ทุกครั้งที่ Node restart และไม่สั่งหยุดโมเดลเมื่อปิด API activate-release ตรวจ startup ก่อนเปลี่ยน symlink แบบ atomic และเก็บ previous ถ้ามี หลังเปลี่ยน release ของ service ที่กำลังรันให้ใช้:

```bash
systemctl --user restart search-tool-api.service
systemctl --user status search-tool-api.service --no-pager
journalctl --user -u search-tool-api.service -n 50 --no-pager
```

status active อาจเป็นช่วงกำลังรอ startup ต้องทดสอบ HTTP จริงด้วย ถ้า bind Tailscale ไม่ได้ให้แก้ Tailscale ก่อน ไม่เปลี่ยน HOST เป็น0.0.0.0

## 6. จัดโมเดลให้รันต่อหลังออกจาก SSH

หากโมเดลเดิมมี service ที่ดูแลอยู่แล้ว ให้ใช้ service เดิมต่อ ไม่เปิด search-embedding.service เพิ่ม

หากเดิมเปิดผ่าน terminal ให้ทดสอบ API ก่อน จากนั้นหยุดเฉพาะโมเดลที่คุณเปิดเองด้วย Ctrl+C และเปิด managed unit หนึ่งครั้ง:

```bash
systemctl --user enable --now search-embedding.service
curl --fail --max-time 5 http://127.0.0.1:8081/health
```

ถ้าพอร์ต8081มี process อยู่ managed unit จะหยุดด้วย78 และเก็บ process เดิมไว้ ไม่ kill หรือ restart แทน การ restart API จะไม่ restart unit โมเดล

ถ้าต้องการให้ user services ทำงานหลัง logout/boot ให้ผู้ดูแลเปิด linger:

```bash
sudo loginctl enable-linger antonius
```

ไม่ใช้ root รัน Node หรือโมเดล หาก user bus ใช้ไม่ได้ให้ตรวจ user manager/XDG_RUNTIME_DIR กับผู้ดูแลก่อน ไม่สลับไปเปิด service root เพื่อข้ามปัญหา

## 7. ตรวจจาก Mac และเครื่องเพื่อน

ในสำเนาที่ build แล้ว ใส่แค่ token ลง .env.peer.local แล้ว:

```bash
node --env-file=.env.peer.local dist/scripts/check-peer-api.js --url http://100.116.20.59:3000
```

ต้องได้ peer_integration_ready ให้เพื่อนรันจากเครื่องของเขาใน Tailscale อีกครั้ง จึงถือว่าส่งต่อเส้นทางจริงผ่าน เก็บ revision วันเวลา และผลตรวจที่ไม่มี token ไว้ ไม่ใช่แค่ภาพ /health ดู [คู่มือผู้เรียก](integration-handoff.md)

## 8. อัปเกรดและย้อนรุ่น

ติดตั้ง revision ใหม่ในโฟลเดอร์ใหม่ก่อน ใช้ activate-release แล้ว restart เฉพาะ API ตรวจ peer อีกครั้ง ไม่เพิ่มสิทธิ์หรือแก้ข้อมูลเพื่อให้ smoke ผ่าน

ย้อนรุ่นที่มี launcher รูปแบบนี้และเคย build แล้วได้ โดยอ่านชื่อ previous:

```bash
readlink ~/search-tool/previous
```

ใช้ revision ของ previous กับ activate-release.sh แล้ว restart API และตรวจ peer เช่นเดิม ถ้าไม่มี previous หรือเป็นโค้ดก่อนมี launcher นี้ ให้ใช้วิธีเปิดโค้ดเดิมที่บันทึกไว้ ไม่อ้างว่าย้อนอัตโนมัติได้

การย้อนโค้ดไม่ย้อนหรือลบสินค้า เวกเตอร์ หรือ migrations ที่แชร์กัน เก็บ release เดิมไว้จนตรวจจากผู้เรียกผ่าน ส่วน token/config อยู่นอก release ต้องทบทวนแยกเมื่อต้องเปลี่ยน

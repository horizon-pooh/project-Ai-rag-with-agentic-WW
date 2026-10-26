# deploy/backup/ — สำรองและกู้ Postgres (D-012)

ข้อมูลทั้งหมดของระบบ (สถานะ bot/human, ข้อความ, คิว procrastinate) อยู่ใน Postgres ตัวเดียว
ไม่มี backup = VM หรือดิสก์พังครั้งเดียวหายหมด รวมถึงรายชื่อลูกค้าที่กำลังคุยกับพนักงาน (AI จะแทรกหลังกู้ระบบ)

> ยังไม่ได้ทดสอบบน Docker host จริง — ทดสอบแค่กับ docker ปลอมบนเครื่อง dev (เข้ารหัส / ถอดรหัส / รหัสผิดแล้วล้ม / ลบไฟล์เก่า / กันลบฐานจริง)

| ไฟล์ | หน้าที่ |
|---|---|
| `pg_backup.sh` | `pg_dump -Fc` → gpg AES256 → `BACKUP_DIR` → rsync ออกนอก VM, ลบไฟล์เก่ากว่า `BACKUP_KEEP_DAYS` |
| `restore_drill.sh <ไฟล์>` | กู้เข้าฐานทดสอบ `restore_check` ใน container เดิม นับแถวตารางหลัก แล้วลบฐานทดสอบ |

## ตั้งครั้งแรก

```bash
# 1. รหัสผ่าน gpg (ยาว สุ่ม) — เก็บสำเนาไว้นอก VM ด้วย (เช่นตู้เซฟรหัสผ่านของบริษัท) ไม่งั้นกู้ไม่ได้
sudo install -d -m 700 /etc/line_prod
openssl rand -base64 48 | sudo tee /etc/line_prod/backup.pass >/dev/null
sudo chmod 600 /etc/line_prod/backup.pass

# 2. ปลายทางนอก VM: ตั้ง ssh key ให้ user ที่รัน cron rsync ไปได้โดยไม่ถามรหัส (รอคำตอบ: เครื่องปลายทาง)

# 3. crontab ของ user ที่สั่ง docker compose ได้ (เวลาไทย 03:30 ช่วงคนน้อย)
30 3 * * * cd /opt/line_prod && BACKUP_PASSPHRASE_FILE=/etc/line_prod/backup.pass BACKUP_REMOTE=backup@HOST:/srv/line_prod deploy/backup/pg_backup.sh >> /var/log/line_prod-backup.log 2>&1
```

ตั้ง `MAILTO=` ใน crontab ด้วย สคริปต์ exit != 0 เมื่อพัง cron จะส่งเมลให้

## ซ้อมกู้ (ต้องทำก่อนขึ้นจริง 1 ครั้ง แล้วเดือนละครั้ง)

```bash
BACKUP_PASSPHRASE_FILE=/etc/line_prod/backup.pass \
  deploy/backup/restore_drill.sh /var/backups/line_prod/line_prod-YYYYmmdd-HHMMSS.dump.gpg
```

ผ่าน = จบด้วย `restore drill ok` และจำนวน users / conversations / messages ใกล้กับฐานจริง
จดวันที่ซ้อม เวลาที่ใช้ และปัญหาที่เจอไว้ท้ายไฟล์นี้

## กู้จริง (ฐานพัง)

1. เปิด kill switch ถ้ายังทำได้ (`docker compose exec worker python -m app.worker.admin kill-switch on`) — call center รับทั้งหมดระหว่างกู้
2. `docker compose stop api worker`
3. ลบฐานแล้วสร้างใหม่ จากนั้นกู้:
   ```bash
   docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   gpg --batch --pinentry-mode loopback --passphrase-file /etc/line_prod/backup.pass -d <ไฟล์> \
     | docker compose exec -T postgres sh -c 'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
   ```
4. **ลบซ้ำลูกค้าที่ขอลบ (PDPA) หลังวันที่ของ backup** — ยังไม่มีบันทึกการลบให้เล่นซ้ำ (ดู D-012 "ยังไม่ทำ") ตอนนี้ต้องไล่จากบันทึกคำขอลบเอง
5. ลูกค้าที่ถูกส่งต่อพนักงานหลังเวลาของ backup จะกลับเป็น `bot` — ถาม call center ว่าใครค้างอยู่ แล้วตั้งกลับเป็น `human`
6. `docker compose up -d api worker` แล้วปิด kill switch

## บันทึกการซ้อม

| วันที่ | ไฟล์ | เวลาที่ใช้ | ผล / ปัญหา |
|---|---|---|---|
| | | | |

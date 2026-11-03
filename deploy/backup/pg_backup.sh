#!/usr/bin/env bash
# deploy/backup/pg_backup.sh
#
# สำรอง Postgres ทั้งฐาน (pg_dump -Fc) เข้ารหัสด้วย gpg แล้วส่งสำเนาออกนอก VM — D-012
# รันบน host ที่มี docker-compose.yml จาก cron ทุกคืน (ดู deploy/backup/README.md)
#
#  - dump ผ่าน container postgres (ใช้ POSTGRES_USER / POSTGRES_DB ใน container ไม่มี secret ในสคริปต์)
#  - ไม่มีไฟล์ dump ที่ไม่เข้ารหัสลงดิสก์: pg_dump | gpg ต่อท่อตรง
#  - backup มีข้อความลูกค้า = ข้อมูลส่วนบุคคล: umask 077, เก็บ BACKUP_KEEP_DAYS วัน
#  - พังตรงไหน exit != 0 (cron ส่งเมลแจ้งได้) และไม่ทิ้งไฟล์ครึ่งๆ ไว้
#
# env:
#   BACKUP_PASSPHRASE_FILE  (จำเป็น) ไฟล์รหัสผ่านของ gpg — เก็บสำเนาไว้นอก VM ด้วย ไม่งั้นกู้ไม่ได้
#   BACKUP_DIR              ที่เก็บบน VM (ค่าตั้งต้น /var/backups/line_prod)
#   BACKUP_KEEP_DAYS        เก็บกี่วัน (ค่าตั้งต้น 14)
#   BACKUP_REMOTE           ปลายทางนอก VM ของ rsync เช่น backup@10.0.0.5:/srv/line_prod
#                           (รอคำตอบ: เครื่องปลายทาง) ว่าง = ไม่ส่งออก + เตือน
#
# สถานะ: ยังไม่ได้ทดสอบบน Docker host จริง (เครื่อง dev ไม่มี docker)

set -euo pipefail
umask 077

cd "$(dirname "$0")/../.."

: "${BACKUP_PASSPHRASE_FILE:?ต้องตั้ง BACKUP_PASSPHRASE_FILE}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/line_prod}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"

[ -r "$BACKUP_PASSPHRASE_FILE" ] || { echo "อ่าน $BACKUP_PASSPHRASE_FILE ไม่ได้" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

stamp="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/line_prod-$stamp.dump.gpg"
tmp="$out.partial"
trap 'rm -f "$tmp"' EXIT

docker compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | gpg --batch --yes --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
        --symmetric --cipher-algo AES256 -o "$tmp"

# dump ว่าง = มีอะไรผิด (gpg ของ stdin ว่างยังได้ไฟล์เล็กๆ) — ฐานจริงต้องได้มากกว่านี้มาก
size="$(stat -c %s "$tmp")"
[ "$size" -gt 1024 ] || { echo "backup เล็กผิดปกติ ($size bytes)" >&2; exit 1; }
mv "$tmp" "$out"
echo "backup ok: $out ($size bytes)"

find "$BACKUP_DIR" -name 'line_prod-*.dump.gpg' -mtime +"$BACKUP_KEEP_DAYS" -print -delete

if [ -n "$BACKUP_REMOTE" ]; then
  # ส่งเฉพาะไฟล์ใหม่ ไม่ใช้ --delete: retention ฝั่งปลายทางตั้งแยก (ต้องไม่เกิน BACKUP_KEEP_DAYS ตาม PDPA)
  rsync -a "$out" "$BACKUP_REMOTE/"
  echo "copied to $BACKUP_REMOTE"
else
  echo "เตือน: ไม่ได้ตั้ง BACKUP_REMOTE — backup อยู่บน VM เครื่องเดียว (VM พัง = หายพร้อมกัน)" >&2
fi

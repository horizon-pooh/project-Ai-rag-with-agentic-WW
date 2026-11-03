#!/usr/bin/env bash
# deploy/backup/restore_drill.sh <ไฟล์ .dump.gpg>
#
# ซ้อมกู้ backup — D-012: backup ที่ไม่เคยลองกู้ไม่นับว่ามี
# กู้เข้าฐานแยกชื่อ $SCRATCH_DB ใน container postgres ตัวเดิม นับแถวตารางหลักให้ดู แล้วลบฐานทิ้ง
# ไม่แตะฐานจริง (ชื่อฐานต้องไม่ตรงกับ POSTGRES_DB) และไม่เขียน dump ที่ถอดรหัสแล้วลงดิสก์ (gpg | pg_restore)
#
# env:
#   BACKUP_PASSPHRASE_FILE  (จำเป็น) ไฟล์รหัสผ่านเดียวกับตอนสำรอง
#   SCRATCH_DB              ชื่อฐานทดสอบ (ค่าตั้งต้น restore_check)
#   KEEP_SCRATCH=1          ไม่ลบฐานทดสอบหลังจบ (ไว้เปิดดูเอง — อย่าลืมลบ มีข้อมูลลูกค้า)
#
# สถานะ: ยังไม่ได้ทดสอบบน Docker host จริง (เครื่อง dev ไม่มี docker)

set -euo pipefail

cd "$(dirname "$0")/../.."

file="${1:?usage: restore_drill.sh <line_prod-YYYYmmdd-HHMMSS.dump.gpg>}"
: "${BACKUP_PASSPHRASE_FILE:?ต้องตั้ง BACKUP_PASSPHRASE_FILE}"
SCRATCH_DB="${SCRATCH_DB:-restore_check}"
[[ "$SCRATCH_DB" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "SCRATCH_DB ต้องเป็น a-z 0-9 _" >&2; exit 1; }

pg() { docker compose exec -T -e SCRATCH_DB="$SCRATCH_DB" postgres sh -c "$1"; }

# กันพลาดไปลบฐานจริง
pg '[ "$SCRATCH_DB" != "$POSTGRES_DB" ]' || { echo "SCRATCH_DB ห้ามเป็นชื่อฐานจริง" >&2; exit 1; }

cleanup() {
  if [ "${KEEP_SCRATCH:-}" != "1" ]; then
    pg 'dropdb --if-exists -U "$POSTGRES_USER" "$SCRATCH_DB"' || true
  fi
}
trap cleanup EXIT

started="$(date +%s)"
pg 'dropdb --if-exists -U "$POSTGRES_USER" "$SCRATCH_DB" && createdb -U "$POSTGRES_USER" "$SCRATCH_DB"'

# --exit-on-error: กู้ไม่ครบ = ซ้อมไม่ผ่าน; --no-owner: ไม่ผูกกับ role ของเครื่องต้นทาง
gpg --batch --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" -d "$file" \
  | pg 'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$SCRATCH_DB"'

# นับแถวให้คนดูเทียบกับฐานจริงคร่าวๆ (ไม่พิมพ์ข้อมูลลูกค้า มีแต่จำนวนและเวลา)
pg 'psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -v ON_ERROR_STOP=1 -c "
  SELECT (SELECT count(*) FROM line_users)          AS users,
         (SELECT count(*) FROM conversations)       AS conversations,
         (SELECT count(*) FROM conversations WHERE status = '"'"'human'"'"') AS human_now,
         (SELECT count(*) FROM messages)            AS messages,
         (SELECT max(received_at) FROM line_raw_events) AS latest_event;"'

echo "restore drill ok: $file ($(( $(date +%s) - started )) วิ)"

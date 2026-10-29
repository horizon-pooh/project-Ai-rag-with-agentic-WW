#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$#" == 0 ]] || { echo 'No arguments expected' >&2; exit 1; }
app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$app_dir"
repo_dir="$(git rev-parse --show-toplevel)"
prefix="$(git rev-parse --show-prefix)"
[[ "$prefix" == 'Search-System/' ]] || { echo 'Run inside tracked Search-System directory' >&2; exit 1; }
git diff --quiet -- . && git diff --cached --quiet -- . || { echo 'Commit tracked changes before packaging' >&2; exit 1; }
revision="$(git rev-parse HEAD)"
# ส่งเฉพาะไฟล์ที่ commit แล้ว ไม่รวม .env, dist, node_modules หรือ reports ที่อยู่นอก Git
while IFS= read -r path; do
 case "$path" in */.env|*/.env.*.local|*/deploy-private/*|*/reports/*) echo 'Private file is tracked; packaging stopped' >&2; exit 1;; esac
done < <(git -C "$repo_dir" ls-tree -r --name-only "$revision" -- "$prefix")
while IFS= read -r entry; do
 [[ "$entry" != 120000* ]] || { echo 'Tracked symlink requires review before packaging' >&2; exit 1; }
done < <(git -C "$repo_dir" ls-tree -r "$revision" -- "$prefix")
output_dir="$app_dir/deploy-private/releases"
mkdir -p -- "$output_dir"
archive="$output_dir/$revision.tar.gz"
[[ ! -e "$archive" && ! -e "$archive.sha256" ]] || { echo 'Release already exists; existing files kept' >&2; exit 1; }
task_temp="$(mktemp -d "$output_dir/.pack.XXXXXX")"
trap 'rm -rf -- "$task_temp"' EXIT
git -C "$repo_dir" archive --format=tar "$revision" -- "$prefix" | gzip -n > "$task_temp/archive"
if command -v sha256sum >/dev/null; then digest="$(sha256sum "$task_temp/archive" | cut -d ' ' -f1)"; else digest="$(shasum -a 256 "$task_temp/archive" | cut -d ' ' -f1)"; fi
printf '%s  %s.tar.gz\n' "$digest" "$revision" > "$task_temp/checksum"
# hard link สร้างไฟล์ใหม่เท่านั้น ไม่เขียนทับ release เดิม
ln -- "$task_temp/archive" "$archive"
ln -- "$task_temp/checksum" "$archive.sha256"
printf 'Release: %s\nArchive: %s\n' "$revision" "$archive"

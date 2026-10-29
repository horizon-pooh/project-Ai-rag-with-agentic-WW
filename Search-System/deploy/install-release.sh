#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$#" == 1 && "$(uname -s)" == Linux && "${1:-}" =~ ^[0-9a-f]{40}$ ]] || { echo 'Use Linux and the full committed revision' >&2; exit 1; }
revision="$1"
[[ "$(node -p 'process.versions.node.split(".")[0]')" == 24 ]] || { echo 'Node24 is required' >&2; exit 1; }
base="$HOME/search-tool"
archive="$base/incoming/$revision.tar.gz"
checksum="$archive.sha256"
[[ -f "$archive" && -f "$checksum" ]] || { echo 'Missing archive or checksum' >&2; exit 1; }
expected="$(cat "$checksum")"
[[ "$expected" =~ ^([0-9a-f]{64})\ \ ${revision}\.tar\.gz$ ]] || { echo 'Unexpected checksum format' >&2; exit 1; }
[[ "$(sha256sum "$archive" | cut -d ' ' -f1)" == "${BASH_REMATCH[1]}" ]] || { echo 'Checksum mismatch' >&2; exit 1; }
task_temp="$(mktemp -d "$base/incoming/.inspect.XXXXXX")"
trap 'rm -rf -- "$task_temp"' EXIT
gzip -cd "$archive" > "$task_temp/archive.tar"
[[ "$(git get-tar-commit-id < "$task_temp/archive.tar")" == "$revision" ]] || { echo 'Archive revision mismatch' >&2; exit 1; }
tar -tf "$task_temp/archive.tar" > "$task_temp/paths"
while IFS= read -r path; do
 [[ "$path" == Search-System/ || "$path" == Search-System/* ]] || { echo 'Unexpected archive path' >&2; exit 1; }
 case "/$path/" in */../*) echo 'Unsafe archive path' >&2; exit 1;; esac
done < "$task_temp/paths"
mkdir -p -- "$base/releases"
release="$base/releases/$revision"
mkdir -- "$release" # มีอยู่แล้วให้หยุด ไม่เขียนทับ release เดิม
chmod 700 "$release"
tar -xf "$task_temp/archive.tar" -C "$release" --no-same-owner --no-same-permissions
cd "$release/Search-System"
npm ci
npm run typecheck
npm run build
npm test
npm prune --omit=dev
printf '%s\n' "$revision" > "$release/.build-complete"
printf 'Built release %s. Current service was not changed.\n' "$revision"

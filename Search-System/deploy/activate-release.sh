#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$#" == 1 && "$(uname -s)" == Linux && "${1:-}" =~ ^[0-9a-f]{40}$ ]] || { echo 'Use Linux and full revision' >&2; exit 1; }
revision="$1"
base="$HOME/search-tool"
release="$base/releases/$revision"
[[ -d "$release" && ! -L "$release" && -f "$release/.build-complete" && "$(cat "$release/.build-complete")" == "$revision" && -f "$release/Search-System/dist/scripts/start-server-release.js" ]] || { echo 'Release build is not ready' >&2; exit 1; }
[[ ! -e "$base/current" || -L "$base/current" ]] || { echo 'current must be a managed symlink' >&2; exit 1; }
[[ ! -e "$base/previous" || -L "$base/previous" ]] || { echo 'previous must be a managed symlink' >&2; exit 1; }
cd "$release/Search-System"
SEARCH_SERVER_CONFIG="$base/config/api.json" node dist/scripts/check-server-startup.js
previous=""
if [[ -L "$base/current" ]]; then previous="$(readlink "$base/current")"; [[ "${previous##*/}" =~ ^[0-9a-f]{40}$ && "$previous" == "$base/releases/${previous##*/}" ]] || { echo 'Previous target requires review' >&2; exit 1; }; fi
task_temp="$(mktemp -d "$base/.activate.XXXXXX")"
trap 'rm -rf -- "$task_temp"' EXIT
if [[ -n "$previous" && "$previous" != "$release" ]]; then ln -s -- "$previous" "$task_temp/previous"; mv -Tf -- "$task_temp/previous" "$base/previous"; fi
ln -s -- "$release" "$task_temp/current"
mv -Tf -- "$task_temp/current" "$base/current"
echo 'Current release changed. Start/restart the API unit and run peer checks next.'

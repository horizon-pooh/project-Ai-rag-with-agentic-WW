#!/usr/bin/env bash
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'Managed model runs on Linux only' >&2; exit 78; }
command -v ss >/dev/null || { echo 'ss is required' >&2; exit 78; }
if [[ -n "$(ss -H -ltn 'sport = :8081')" ]]; then
 echo 'Model port is already in use. Existing process kept; leave this unit disabled when reusing it.' >&2
 exit 78
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export LLAMA_EMBEDDING_PORT=8081
export LLAMA_CONTEXT_SIZE=2048
exec bash "$script_dir/start-embedding-server.sh"

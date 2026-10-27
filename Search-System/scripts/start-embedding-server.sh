#!/usr/bin/env bash
set -euo pipefail
# Run on BPLUTONIX, separately from the Fastify process. Never auto-stop another model.
llama_binary="${LLAMA_SERVER_BIN:-$HOME/ai/llama.cpp/build/bin/llama-server}"
model_file="${LLAMA_MODEL_FILE:-$HOME/ai/models/Qwen3-Embedding-0.6B-Q8_0.gguf}"
port="${LLAMA_EMBEDDING_PORT:-8081}"
context="${LLAMA_CONTEXT_SIZE:-2048}"
device="${LLAMA_DEVICE:-Vulkan0}"
[[ "$port" =~ ^[0-9]{1,5}$ ]] && ((10#$port>=1 && 10#$port<=65535)) || { echo 'Invalid model port' >&2; exit 1; }
[[ "$context" =~ ^[0-9]{1,5}$ ]] && ((10#$context>=128 && 10#$context<=32768)) || { echo 'Invalid context size' >&2; exit 1; }
command=("$llama_binary" -m "$model_file" --embedding --pooling last --host 127.0.0.1 --port "$port" --device "$device" -ngl 99 -c "$context")
if [[ "${LLAMA_START_DRY_RUN:-0}" == 1 ]]; then printf '%q ' "${command[@]}"; printf '\n'; exit 0; fi
[[ "$(uname -s)" == Linux ]] || { echo 'Run embedding:serve on the Linux model server, not on the Mac' >&2; exit 1; }
[[ -x "$llama_binary" && -f "$model_file" ]] || { echo 'Missing llama-server binary or GGUF file' >&2; exit 1; }
command -v ss >/dev/null || { echo 'ss is required to check the existing listener' >&2; exit 1; }
if [[ -n "$(ss -H -ltn "sport = :$port")" ]]; then
 echo "Port $port is already bound. Existing process kept; this command did not launch or restart anything."
 echo 'Verify it with embedding:check -- --live through the correct local endpoint.'
 exit 0
fi
# Keep the current working setup: Vulkan0, GPU layers99, last pooling, context2048 by default.
exec "${command[@]}"

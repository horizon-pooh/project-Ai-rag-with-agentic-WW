# Local Qwen3 embedding provider — revised Commit 18

This replaces the uncommitted OpenRouter implementation. The only provider is llama_cpp at a loopback
HTTP endpoint, directly on BPLUTONIX or via an SSH tunnel from the Mac. No external embedding API/key is used.
The keyword API, SQL schemas and semantic_available=false remain unchanged; no database registration,
vector writes, backfill or hybrid ranking is introduced in this commit.

## Verified live setup

Observed2026-10-27 through the user's existing server:
- Model ID /home/antonius/ai/models/Qwen3-Embedding-0.6B-Q8_0.gguf; GGUF Q8_0.
- llama.cpp build b11753-d8d92973c;1024-dimensional vectors, runtime context2048, four existing slots.
- Server8081 bound to127.0.0.1; Mac18081 forwards to that loopback listener.
- /health, /v1/models, /props, /tokenize and /v1/embeddings respond. Small batches are supported.
- Last pooling is specified by the user's command and the checked-in launcher. /props does not independently
  report pooling, so runtime inspection alone is not proof of that flag.

The architecture supports a larger context, but this process is configured for2048. Do not treat the
model's advertised training context as the runtime input limit. A701-token real inference passed; this is
not a full-catalog context/load benchmark. No model restart or running-service replacement was performed.

## Interface and identity

`createLlamaEmbeddingProvider({profile, baseUrl, apiKey?, bounds?, runtime?})` returns
`embed({texts, purpose: "query" | "document", signal?})`.
Profile and bounds are copied and text captured before transport. Runtime injection is for offline tests.

Base URL must be http://127.0.0.1:<port>/v1 (localhost/IPv6 loopback also supported); credentials/query/fragment
and arbitrary remote hosts are rejected. Redirects are not followed. On Mac use18081; on the model server
use8081. Transport location is excluded from the semantic configuration hash so the two access paths can
use the same profile. Never let the public caller supply this URL, profile, auth key or raw vectors.

Profile identity includes provider/model/dimensions/cosine/version, artifact filename, llama runtime version,
last pooling, server L2 normalization, context and query/document prefixes. Query and documents use the SAME
profile identity with an explicit paired purpose policy. This is a configuration hash, not a cryptographic
GGUF weight checksum or document content_hash. Commit19 must record/verify the actual artifact checksum,
storage limits and model settings before registering or reusing a DB model ID. Never reuse an old OpenRouter
1536-dimensional model row for these1024-dimensional vectors.

The query prefix is an explicit English retrieval instruction:
`Instruct: Given a product search query, retrieve relevant product descriptions\nQuery:`
Documents have no prefix. The application adds this exactly once; it does not call a chat template or send
OpenRouter input_type/dimensions fields. No text trim/case normalization, silent truncation, client-side
vector normalization or model fallback occurs. Instruction quality remains to be evaluated, not assumed optimal.

## Request sequence and context protection

One logical attempt, under one deadline/abort signal:
1. GET /v1/models verifies the configured ID, metadata dimensions and runtime context.
2. GET /props verifies runtime build, artifact basename and reported context.
3. POST /tokenize once per prefixed input with add_special=true, parse_special=true and no pieces.
   Zero/malformed tokens fail; more than2048 tokens fails input_too_long BEFORE embedding inference.
4. POST /v1/embeddings with model, string-array input and encoding_format=float.

The response must contain the exact model, list envelope, one unique integer index per input and exact width.
Vectors are reordered by index. Every component must be finite and float32-safe, nonzero after float32
conversion, and the server-returned L2 norm within0.01 of1. The application never rescales a bad vector.
Prompt/total usage must be safe nonnegative integers. Prompt usage must equal the sum of actual preflight
token counts; mismatch fails rather than hiding truncation. Live query/document smoke confirmed this equality.
No missing usage is represented as zero.

Tokenizer requests are per input; product database queries remain batched independently. This version favors
verification over caching. It does not claim an optimal tokenizer/network request count or throughput.

## Bounds, retry and errors

Defaults:16 inputs,16000 UTF8 bytes per prefixed input,128000 input bytes per batch,4MiB response body,
3 logical attempts,10s per attempt and30s total budget. Backoff starts250ms with jitter, maximum5000ms wait;
configuration caps attempts at5 and timeouts at60s. Byte caps are protection, not a substitute for the actual
tokenizer checks. Large inputs are rejected, never silently split/truncated.

HTTP408/429/5xx, network errors and timeouts can retry within the budget. Permanent HTTP failures,
model/config mismatch, invalid response, over-context, bad indices/floats/norms/usage stop immediately.
Retry-After seconds/dates are honored; a requested wait above the retry cap or remaining time stops instead
of retrying early. Timers cover guard requests, tokenization, embedding response/body; cancellation also
stops sleep. Timing budgets depend on event-loop scheduling and are not hard real-time guarantees.

`attempts` counts logical rounds, each possibly containing several guard/tokenizer HTTP calls.
Retry may repeat local GPU work, even if a timed-out request completed on the server; cancellation does not
prove GPU computation was undone. Returned usage is for successful inference, not a count of all server work.
There is no cloud billing API in this implementation, but server resources still have a cost.
EmbeddingError retains only a fixed code, attempt count and optional HTTP status, without raw bodies,
keys, text or Error causes. The adapter logs none of those values.

## Private profile and smoke

The standalone check reads only .env.embedding.local with dotenv.parse, not the search API .env.
The example is prefilled with the OBSERVED server model/configuration; update it intentionally when changing
model/build/context and change semantic version as appropriate. Normal startup does not load this profile.
The optional LLAMA_EMBEDDING_API_KEY is used only if the local server later enables auth; currently no key
is needed. OPENROUTER_API_KEY is not read. Keep private files ignored even when they currently have no secret.

`npm run embedding:check` is dry-run, with0 network/provider calls and0 database writes.
`npm run embedding:check -- --live` uses the local model for two short document texts and one instructed query,
maximum one logical attempt per call. It verifies both purposes without downloading catalog data or printing
keys/full vectors. A real run passed with document token counts28/29 and query28, matching usage57/28.
The real tokenizer also rejected a2101-token input with0 embedding requests. No vectors were stored.

## Model process commands kept in this repository

- `npm run embedding:tunnel`: explicit Mac SSH18081->BPLUTONIX loopback8081; ExitOnForwardFailure enabled.
- `npm run embedding:serve`: run the checked-in launcher on Linux/BPLUTONIX.
- `npm run embedding:serve:remote`: SSH into BPLUTONIX and run the previously copied launcher.

One-time copy for the remote command:
`scp scripts/start-embedding-server.sh antonius@100.116.20.59:~/ai/start-search-embedding.sh`

The launcher preserves the user's command: model path, --embedding, --pooling last, host127.0.0.1,
port8081, deviceVulkan0, -ngl99, -c2048. Binary/model/device/context/port can be overridden with documented
LLAMA_SERVER_BIN/LLAMA_MODEL_FILE/LLAMA_DEVICE/LLAMA_CONTEXT_SIZE/LLAMA_EMBEDDING_PORT environment values.
`LLAMA_START_DRY_RUN=1 npm run embedding:serve` only prints the command, including on Mac.

Real start is Linux-only and checks binary/file and listening port. If8081 is already bound it leaves the
existing process intact and exits without loading another copy. A bound port is not proof of the right model;
use the live adapter check to validate identity. No kill/restart/install/systemd action is automatic.
On an available port the launcher runs in the foreground; a persistent service manager belongs to deployment
work. It is not started inside app.ts, avoiding extra GPU processes when the API restarts.

## Remaining work

Commit19 audits migrated embedding storage/models/freshness/worker permissions for this LOCAL1024-dimensional
profile. Commit20 adds full dataset token/context estimates, durable batch/resume/backfill and a small real-data
trial. Some detailed products may exceed2048: evaluate that before bulk work rather than discarding content.
Later commits connect category/product vectors and hybrid results. Do not claim semantic search just because
this adapter has been tested.

References:
- [llama.cpp server API and launcher flags](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- [Qwen3-Embedding-0.6B model and instruction format](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)

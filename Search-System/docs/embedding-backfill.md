# Embedding registration and bounded backfill (Commit20)

This is an operator CLI, not an API route or a worker automatically started by Node.
The Tool API remains keyword-only until subsequent category-semantic/product-RRF commits.
Existing product/category storage and migration0007 are reused; this commit adds no migration,
products, draft publication, source-text edits, or vector index.

## Process and identity

1. Compute the actual GGUF file SHA256 on BPLUTONIX and keep the model file unchanged while serving it.
2. Start the existing local Qwen/llama.cpp model process if not already running. Wait for /health.
3. On Mac start the model tunnel18081→8081 and DB tunnel15432→5432 in separate terminals.
4. Run Node registration/worker after the model is ready. On the server use loopback8081 in the private profile.

Registration uses only owner credentials from .env.migration.local. It verifies runtime model/build/context
via metadata/tokenizer, takes a transaction-scoped advisory lock, and registers an explicitly supplied EMnnnn
with active=true. The settings store the complete semantic profile, its configuration_hash and actual
artifact_sha256. Exact registration reruns are harmless; conflicts/inactive records are never overwritten,
and existing configurations are never automatically deactivated. No vector API call occurs at registration.

EMBEDDING_ARTIFACT_SHA256 must be entered in .env.embedding.local by the operator after `sha256sum`.
It is a file digest, not the configuration hash. The current llama.cpp HTTP metadata does not attest to the
loaded file's cryptographic digest; the operator must ensure the hashed artifact is the same file loaded
by the server. Replacing a file/configuration requires a new verified model configuration, never relabeling old vectors.

Backfill uses only embedding_worker credentials from .env.embedding-worker.local. It rechecks scoped identity,
requires an explicit active model ID with exactly matching provider/model/dimensions/metric/settings, and
uses that signature again in claim/finish statements to fence configuration changes after initial validation.
It does not use the owner pool for generation or export document text/vectors to logs.

## CLI

```
npm run embedding:register -- --model-id EM0001 --activate
npm run embedding:backfill -- --kind category --model-id EM0001 --limit 20
npm run embedding:backfill -- --kind product --model-id EM0001 --limit 20
npm run embedding:backfill -- --kind category --model-id EM0001 --limit 3 --max-tokens 10000 --max-seconds 120 --write
```

Without --write this is a dry-run: actual model metadata/tokenizer are called, but no embedding inference or
job/vector database write is performed. No token counts are estimated from characters. Context oversize
is reported as input_too_long with the real count. Empty/UTF8-byte-limited inputs are reported separately.
Source text hash/template validation occurs before inference; no truncation or shortening is performed.

Dry-run source pages are bounded to100 rows (repository limit200). A report contains aggregate coverage,
bounded issues (first200 with issues_truncated), largest inspected token count, eligible counts and tokens.
Over-context inputs do not consume the eligible-token budget. Dry-run covers the selected bounded snapshot
of changing sources, not a frozen export or a claim of full-corpus coverage when stopped by limit/time/budget.

--limit: default20 dry-run,3 write;1..20000 dry-run; at most1000 documents per write invocation.
--max-tokens: default100000; upper bound on eligible document input tokens per invocation.
--max-seconds: default120;1..3600; aborts HTTP at deadline with bounded DB-query cleanup.
--after: optional last processed ID from report.next_after; numeric Article for products, six-character category ID.
Unknown/duplicate flags, missing values and invalid numeric types fail closed.

Use next_after to continue a long scan past oversize inputs; token-budget stop keeps the unprocessed ID
eligible for the next scan. Reset --after (omit it) to revisit earlier failures/source edits later. Cursor use is
operator controlled and is not a promise that earlier rows are now fresh. Every report includes coverage for
all visible sources in the selected kind/model, independently of the cursor. source_exhausted means the current
scan ended; it does not mean pending leases, terminal failures, oversize records, or future retries are complete.

## Claim / finish and resume

Single-statement INSERT...ON CONFLICT atomically creates/reclaims one pending job with random UUID lease,
DB-clock expiry120sec and monotonically incremented attempt count. A live lease or ready/current row is skipped.
Fresh vectors are excluded in candidate scans; new source hash/template resets the attempt budget. No transaction
is held while tokenizing/embedding. Inference concurrency is1 per process; the worker pool is capped at2.

Completion is a compare-and-set UPDATE matching model signature, key, hash, template, pending status, attempt,
lease UUID and unexpired DB-clock lease. Source freshness and publication are checked again, and the existing
migration0007 guard triggers enforce source/model locks and vector dimension/normalization. Expired/superseded
completions return lost_lease instead of overwriting a newer job. If source edits contend with completion,
PostgreSQL can resolve a lock deadlock by failing one transaction; the CLI stops on DB uncertainty and the
committed lease/freshness state is inspected on resume. It never overwrites an uncertain committed result as failed.

The backfill CLI configures exactly ONE provider attempt per claimed job. Transient transport/408/429/selected5xx
failures persist a safe code and next_retry_at (30,60sec), with at most3 job attempts per unchanged source/model.
No nested three-by-three inference retries; no busy waiting. Terminal malformed output/model mismatch stops the
run, persists terminal failure for that job and requires investigation. Invalid-response/wrong-profile rows cannot
become ready. A process crash leaves a lease that expires and can be reclaimed. SIGINT aborts HTTP and attempts
bounded cleanup, or leaves an expiring lease if interrupted before completion.

embedding_requests counts calls made by the worker to the adapter, not proof of GPU executions or provider HTTP
requests. successful_provider_attempts and embedding_tokens are known successful adapter results only; failed
or interrupted HTTP work may have consumed local compute. No external-provider price estimate is reported.
`total_tokens` counts eligible inspected input tokens (including possible claim races), not total HTTP traffic.

Retries without --after revisit due earlier work. Terminal failed rows/exhausted retries are intentionally not
reset automatically. Use a reviewed operator repair after fixing the cause; a new semantic configuration needs
a new model ID. Never bulk reset leases/attempts while workers are running. Never rollback0007 after real job
progress just to reset this worker; its down migration protects progress metadata.

## Coverage and acceptance

Fresh means current active model + source hash/template + dimension, under the worker's RLS scope. Category
sources include parent and leaf categories. Product sources include only published products. Drafts remain drafts.
Inspect before/after coverage, stopped reason, errors, pending/failed/stale and oversize counts. Do not report
vectors for all18,000 products or hybrid quality until those tasks have actually happened.

Start with dry-run20 per kind → dry-run full sources within explicit time/token bounds → smoke3 per kind → review
coverage/live output → bounded larger runs. If many inputs exceed2048 tokens or16000 bytes, stop bulk generation
and design/version a compact embedding_text separately; do not silently change source text in this commit.

## Tests

`npm run typecheck`, `npm run build`, `npm test` include the worker/registry/inspection regression suite.
Default tests use isolated PGlite/vector0.8.1. Optional native tests use a NEW disposable cluster with
PostgreSQL18.6/vector0.8.2, fixtures, roles and exact database name `catalog_embedding_test_c20` with test admin
`catalog_test_admin`; require private Unix socket under /private/tmp and no production connection settings:

```
EMBEDDING_BACKFILL_TEST_MODE=native \
EMBEDDING_STORAGE_TEST_HOST=/private/tmp/YOUR_TEST_CLUSTER/socket \
EMBEDDING_STORAGE_TEST_PORT=55440 \
node --import tsx --test tests/embedding-backfill.test.ts
```

The native suite includes two simultaneous sessions racing for a single lease. Fixtures mutate/drop tables and
create roles; NEVER point this at the catalog DB or create the test admin there. A fresh cluster/database and
fixture roles are needed for each run. Native is an additional check, not a production backfill.

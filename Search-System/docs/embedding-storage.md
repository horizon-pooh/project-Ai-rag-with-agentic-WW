# Existing embedding storage and worker access — Commit19

## Audited baseline and scope

Live read-only audit2026-10-27 found PostgreSQL18.6 with extensions.vector0.8.2. The existing
embedding_models/product_embeddings/category_embeddings tables are empty; category_search_documents has2003
rows and product_search_documents18000. Vector columns are unconstrained `extensions.vector`, so1024-dimensional
Qwen vectors do not require replacing tables or changing a fixed1536-dimensional column.
The configuration unique index, category source hashes, model-used-configuration guard and source triggers
already exist. search_api remains a published-only reader and cannot directly read embedding storage.
The catalog owner antonius cannot CREATE ROLE, so the separate administrator bootstrap is required.

Migration0007 reuses storage, keys and indexes. It adds six product job fields (created/updated timestamps,
attempt count, retry time, lease token/until), analogous to existing category fields, with lease checks.
Existing-row timestamps, if any, would reflect migration time, not the original generation time.
No product/draft/publication change, model registration/activation, vector generation/backfill, search route
change or vector index is included. Registration remains an explicit prerequisite for Commit20.

## Invariants

Both ready-vector guards lock model and source rows, require an active model, correct native width,
current source hash/template, nonzero cosine vector and the pre-existing ready payload/timestamp constraints.
The vector extension rejects non-finite components. For llama_cpp, ready requires a configuration_hash and
artifact_sha256 (64 lowercase hexadecimal characters) and server-L2 norm within0.01 of1.
Metadata format is not proof of the actual GGUF file: the operator must verify its checksum before registration.
The audit compares an active model's provider/name/dimension/metric AND configuration hash against the selected
profile. It does not accept a copied hash on a different model. Stored configuration cannot change once ANY
vector/job row references the model; activation can change, and inactive vectors are not counted fresh.

Changing a document hash or template stales ALL related jobs/vectors, including pending/failed rows,
and clears leases. Keyword-only changes without hash/template change do not force embedding again.
This freshness contract is relative to the stored search document. It does not automatically rebuild product
search documents for every product/spec edit; catalog editing must keep those source documents current.
Current category-source refresh behavior is reused unchanged.

Non-pending status clears lease automatically. A lease requires both token and expiry, pending status and
positive attempt count. These fields prepare resumption, but are not yet a fenced claim/finish worker:
Commit20 must use transaction/lease-token CAS, short model→document→job locks, bounded attempts and stale-job
handling; never hold a DB transaction during HTTP inference. No concurrency or exactly-once generation claim
is made merely because lease columns exist.

## Roles and narrow definer triggers

Administrator-run docs/embedding-worker-roles.sql creates a NOLOGIN catalog_embedding_worker group and a
separate embedding_worker login, rejects unsafe flags/unexpected parent memberships, grants CONNECT and caps
connections at2. Password is set with psql `\password`, never in this file or Git. API/admin credentials are
not reused. Its runtime pool uses loopback/SSH, separate .env.embedding-worker.local, read-write mode for
vector jobs, query/lock/idle transaction timeouts and no dynamic URL parameters.

Worker can:
- Read published product IDs/category/status and product search documents, all category search documents,
  model metadata and in-scope embedding rows.
- Insert product/category embeddings and update their payload/hash/template/status/job fields.

Worker cannot edit product/business/source/model data, change vector identity keys, DELETE/TRUNCATE vectors,
create objects in public, own catalog tables, inherit unrelated roles, be superuser/replication/BYPASSRLS,
or register/activate a model. Restrictive published guards also resist later broad permissive read policies.
There are no anonymous/API grants on the private model/vector tables.

The four existing trigger functions become narrow SECURITY DEFINER triggers owned by the verified catalog
owner, with empty search_path and fully qualified objects; PUBLIC execute is revoked. Trigger bodies cannot
be called as a general SQL API. Only table owners may attach them. This permits model/source FOR SHARE locks
without granting worker UPDATE on model/source columns. It does NOT bypass the caller's row-security check
for vector writes. No general definer write endpoint or dynamic SQL is introduced.
Migration preflight checks the audited old function definitions by SHA256 and refuses unreviewed drift.
Used-model immutability is reused from the existing model trigger. Worker data/privilege checks are real role
queries; runtime identity rejects unexpected source write grants and vector delete/key-update privileges.

## Audit commands

`npm run db:check-embedding-storage` loads private owner connection configuration and the local embedding
profile, then runs READ ONLY. It inspects extension/columns/trigger flags, counts and model-relative freshness,
without returning source text or vector payloads. Status embedding_storage_ready means schema/guards are ready;
model_registration_required=true is expected while no verified active profile exists. Fresh counts include
ready status, active model, current source and valid dimension/norm/metadata; ready alone is not coverage.

`npm run db:check-embedding-worker` uses the NEW login and private worker config, checks actual role flags and
privileges, then reads scoped counts inside READ ONLY. Expected embedding_worker_ready, visible_drafts0,
writes_performed0 and backfill_started=false. No real test vector is inserted to prove write access.
An empty model registry is not fabricated as ready semantic search. The operator must configure the worker
password locally before this check; do not paste passwords into chat.

## Rollback and model registration

Down restores only this migration's policies/grants/functions/job columns, preserving model/source/vector
rows and existing table identities. It refuses to discard nonzero product job-attempt/retry/lease state;
use a reviewed forward repair after real jobs have started. Original effective PUBLIC trigger-function
EXECUTE ACLs are restored from the audited baseline. Global roles/passwords are not deleted by Down.
Do not edit applied migrations or bypass checksums to make rollback succeed.

No database model ID is chosen here. Before Commit20 registration, record the actual model artifact checksum:
`ssh -t antonius@100.116.20.59 'sha256sum ~/ai/models/Qwen3-Embedding-0.6B-Q8_0.gguf'`
The configuration hash from Commit18 is NOT that checksum. Compare the actual active local profile/model/build
and register/reuse a matching immutable model ID deliberately; never reuse a1536-dimensional OpenRouter row.
The native file checksum has not been read through HTTP: llama-server model metadata does not expose it.

Keep the user deployment prerequisite: Qwen3 process healthy on server8081 BEFORE starting Node API/worker
there. Reuse an already running model. Mac uses its separate18081 SSH forward. This commit does not install a
service, restart a model or enable semantic search in the keyword API.

## Verification

The full default suite uses PGlite PostgreSQL18.3 and its compatible pgvector0.8.1 package
@electric-sql/pglite-pgvector0.0.9, pinned to the existing PGlite0.5.8 peer. Do not call it the live extension version.
The SAME54 storage/access tests also passed separately on a disposable native PostgreSQL18.6 cluster with
pgvector0.8.2, pgcrypto and pg_trgm, built from official sources. No app DB/roles were changed by those tests.
See native-embedding-tests.md for isolation requirements; the native test harness refuses the production DB.
It is not a full model/context/quality/performance or simultaneous multi-session race benchmark.

References:
- https://www.postgresql.org/docs/18/ddl-rowsecurity.html
- https://www.postgresql.org/docs/18/sql-select.html
- https://github.com/pgvector/pgvector
- https://pglite.dev/extensions/

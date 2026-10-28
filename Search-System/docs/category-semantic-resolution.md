# Category semantic resolution — Commit21

## Boundaries

This commit adds category query embeddings and exact vector-distance ranking INSIDE PostgreSQL,
combined with existing lexical/ID evidence and hierarchy. It does not add product-vector ranking,
RRF, HNSW, public vector inputs, model registration, corpus backfill, product publication or source edits.
The existing request JSON v1 remains unchanged. Caller category/feature/attribute IDs retain their verified fast path.

API semantic_available remains false because PRODUCT search still ranks by keyword. If category semantics selects
a scope, category_semantic_scope_selected explicitly states that category evidence used vectors while product
ranking remains keyword-only. Existing semantic_unavailable warning is retained, not falsely removed.

## Flow

1. Normal v1 validation, authentication and verified ID/name selectors run first.
2. Exact unambiguous labels/aliases/codes and existing ambiguity/identity locks take precedence. Caller category_ids
   never invoke semantic inference, including invalid IDs or name/ID conflicts.
3. Only unknown/similar lexical groups, including a fuzzy candidate overflow without exact labels, may request
   semantic assistance. Exact ambiguous aliases or explicit unknown category codes require clarification.
4. A short read-only SQL snapshot probes exact model configuration and fresh category coverage. A model must be
   active and match the configured model ID/configuration hash/actual artifact SHA256. ALL current category rows
   must have a source document and fresh ready dimension-correct normalized vector. Missing/stale/partial coverage
   returns a state, not biased scores. No query embedding is requested while coverage is incomplete.
5. Release snapshot and checked-out connection BEFORE embedding the category phrases. Use the current Qwen profile,
   purpose=query and the existing explicit query prefix. Never use semantic_query to override category_query.
   All unresolved OR phrases share a5sec abort deadline; adapter maxAttempts1. Never spawn a model or tunnel per request.
6. Start the final repeatable-read read-only search snapshot. Rerun lexical selectors, so newly exact evidence wins.
   SQL rechecks model/freshness in this final snapshot. A complete prepared phrase set is applied or discarded on failure.
7. SQL ranks fresh level3 categories by exact cosine distance and returns at most8 candidates (SQL supports2..10),
   deterministic by category ID on ties. No product rows or vector payloads are loaded into Node for ranking.
8. Semantic suggestions or calibrated selected scopes flow through the existing typed-filter/keyword SQL and batch
   fact hydration. All publication/RLS, spec/unit, alternatives, identity, ordering and paging rules remain intact.

## Specificity anchors and ambiguity

Existing category names/aliases literally present in a phrase anchor its allowed scope. Thai labels use substring
matching; ASCII labels use alphanumeric boundaries. Longer overlapping labels and descendant labels take precedence.
For example an actual OLED alias in the catalog anchors OLED, not QLED; NEO QLED is not reduced to QLED; TV mount
labels do not widen to televisions. Anchors come from catalog metadata, not invented hardcoded category IDs.

Several distinct anchors inside one unsplit phrase require clarification even if a score wins. Explicit OR separators
(`|`, `หรือ`, ` or `) are resolved as separate phrase groups and selected scopes are unioned. Broad exact parent labels
continue to expand ALL valid leaf descendants using existing hierarchy. Semantics is a backup, not a conversational
negation/parser rewrite; the chat AI should send positive category intents and structured filters.

## Default mode: suggestions

The feature is globally OFF unless SEARCH_CATEGORY_SEMANTIC_ENABLED=true. Once enabled, automatic semantic selection
is still OFF by default. Semantic ranking replaces uncertain lexical suggestions with bounded category IDs/names
in needs_clarification. The chat AI may use an appropriate candidate ID on its next Tool call when intent is clear,
or ask the customer when it is ambiguous. Scores remain internal diagnostics and are NOT confidence probabilities.

Automatic mode exists but requires explicit, measured SEARCH_CATEGORY_SEMANTIC_MIN_SCORE,
SEARCH_CATEGORY_SEMANTIC_MIN_GAP and SEARCH_CATEGORY_SEMANTIC_CALIBRATION_ID. There are no guessed production defaults.
Fixture scores0.8/0.1 in tests are synthetic and must NOT be copied as production recommendations.
Measure/evaluate with this Qwen quantization, query prefix and catalog, including synonyms, unrelated products,
accessories, broad requests, near subtype ties and multiple OR groups. Record calibration before enabling it.

In calibrated mode, a top score must pass the measured minimum and top-vs-second gap; two distinct anchors still
require clarification. A singleton anchored scope needs the score cutoff, not a fabricated runner-up gap. Exact
ambiguity or an identity selector is never overridden. Unresolved multi-group requests never become a partial scope.
Full production calibration/search quality evaluation is later in the plan; this commit supplies the mechanics and
safe default without claiming vector quality from storage readiness or one successful model call.

## SQL capability and migrations

0008 adds two versioned STABLE read-only SECURITY DEFINER functions, owned by the migration owner, with empty
search_path and fully qualified table/operator references:

- catalog_search.category_semantic_state_v1(model_id,configuration_hash,artifact_sha256)
- catalog_search.category_semantic_candidates_v1(phrase,query_vector,model_id,configuration_hash,artifact_sha256,limit)

PUBLIC execute is revoked. catalog_search_reader receives only function EXECUTE. It receives no table SELECT on
models, source embedding documents or stored vectors. Results contain bounded category metadata/scores/counts,
not vectors/weights/settings/customer text. Trusted internal Node supplies query vectors; HTTP schema still rejects
caller vectors/provider settings. The owner capability is necessary to read private vector tables without exposing
them to the runtime role, and contains no dynamic SQL, writes or arbitrary table identifiers.

Missing model/coverage are regular fallback states. Invalid SQL inputs fail closed. Database/query errors propagate
as search_unavailable rather than fabricated no_matches. Provider errors keep lexical evidence and a diagnostic;
no partial vectors from an incomplete OR request are used. No broad permission fallback or owner runtime is added.

Down drops only these functions; models/vectors/source text/existing resolvers remain intact. Disable the feature
before rolling back0008; enabled new code without its functions is a deployment error, not permission to hide DB errors.

## Local setup

Keep model on BPLUTONIX ready before Node. Use existing model/DB tunnels on Mac; reuse running processes.
Copy .env.category-semantic.example to ignored .env.category-semantic.local. Existing .env.embedding.local keeps the
verified semantic profile and actual artifact SHA. No new credentials are needed.

`npm run dev:semantic` loads .env + .env.embedding.local + .env.category-semantic.local, then runs the existing server.
`npm run dev` remains the normal entry point and does not automatically read these extra private files; deployment
later must load the same configuration explicitly. Existing process environment takes precedence over Node env files.

Read-only check:

```
npm run db:check-category-semantic -- --query "ทีวี OLED สำหรับดูหนัง"
```

It uses the real search login, probes readiness and returns lexical/semantic diagnostic data, never writes sources
or vectors. category_semantic_fallback with coverage_incomplete is expected until ALL2,003 current categories are
fresh. Existing lexical search works meanwhile. A readiness result does not prove ranking accuracy.

## Verification

Default full tests include the new semantic suite plus all prior contracts/API/filter/repository/provider/storage
regressions. Native fixture tests additionally target PostgreSQL18.6/vector0.8.2 in a NEW disposable cluster:

```
CATEGORY_SEMANTIC_TEST_MODE=native \
CATEGORY_SEMANTIC_TEST_HOST=/private/tmp/YOUR_TEST_CLUSTER/socket \
CATEGORY_SEMANTIC_TEST_PORT=55441 \
node --import tsx --test tests/category-semantic.test.ts
```

Fixed native test DB catalog_category_test_c21, admin catalog_test_admin, private Unix socket only. It creates roles,
fixtures/migrations and edits disposable data. NEVER point tests at the catalog DB. Tests cover confidentiality,
coverage, subtype/accessory anchors, score ties, OR composition, exact/ID precedence, source/model changes,
provider errors, no SQL transaction/connection across HTTP, v1 output honesty and rollback/reapply preservation.
This is not a full corpus evaluation, production benchmark or product RRF implementation.

# Category resolution — Commit 12

Internal category routing only. No new HTTP route, product search, typed spec
resolution, embeddings, product data change or publication change.

## Inputs and outputs

`resolveCategoryQuery(repository, categoryQuery)` consumes a nonempty literal
category label/ID or an explicit OR list from `/filters/category_query`.
The entire string is at most 200 Unicode code points. Use `หรือ`, a spaced
English `or`, or `|` for up to five phrases. `และ`, commas and slash are not
implicitly split: these can be part of a category name. Duplicate identical
phrases are coalesced. Arbitrary customer sentences and spec extraction are
outside this commit; the chat caller should populate the dedicated category
field. Do not silently ignore qualifiers remaining in a long query.

Examples:

- `ทีวี` → exact alias for TV0100 → all level-3 descendants by parent IDs.
- `ทีวี OLED` / `OLED` → TV0108 only, no QLED siblings.
- `TV0109` → that category alone. An unknown ID is never corrected by aliases.
- `ทีวี OLED หรือ TV0109` → union of the two scopes, leaf IDs deduplicated.
- A misspelling → ranked similar-label suggestions with needs_clarification,
  empty selected category_ids. The caller can confirm an actual name or ID.
- One unresolved OR branch → needs_clarification for the whole request;
  successfully resolved branches remain diagnostic only, never a partial filter.

The internal result has `resolver_version`, `source_path`, per-phrase evidence,
`category_ids` and `scopes` (root/leaf IDs plus the full three-level path).
It is not a Tool API v1 response. Commit 16 maps the internal result to the
existing clarification/result contracts. Do not expose additional internal
fields by bypassing strict response validation.

## Evidence and ambiguity

Migration 0003 adds two SECURITY INVOKER functions under the existing private
catalog_search schema. Existing aliases/views/RLS are reused, not copied.

`category_candidates(text, integer)`:

1. An ID-shaped query is locked to the exact category ID, case-insensitively.
2. Compare normalized name/alias labels exactly. Any exact match suppresses
   all fuzzy candidates, preserving OLED/NEO QLED/accessory specificity.
3. If no exact match exists, use existing `extensions.similarity` from pg_trgm.
   Labels at score >= 0.35 may appear as suggestions; fewer than three query
   characters and unknown ID-shaped queries have no fuzzy fallback.
4. Return each category once with matched label/source, match kind, score,
   and total candidate count. Sort by score then ID, at most eight via the adapter.

0.35 is an initial candidate-generation setting, not an accuracy guarantee,
confidence probability or permission to auto-correct. Trigram matching measures
spelling similarity, not meaning. Thai combining marks/segmentation and very
short words need explicit aliases or later semantic support. Measure and tune
against the evaluation corpus in Commit 24. No model/provider is called.

Exact collisions are not settled by the first database row. The resolver expands
all exact candidates (bounded by eight per phrase, forty roots per request).
If their leaf sets are identical, it coalesces them without changing scope and
records equivalent roots. If leaf sets differ, it asks for clarification.
This handles same-named parent/child food categories without discarding a branch.
If total candidates exceed the returned limit, it reports candidate_limit_exceeded
and never silently applies the truncated list. Unknown and empty scopes also
produce clarification, not product no_matches.

`category_leaf_scopes(text[])` walks actual parent IDs with increasing levels
and returns only level 3. It checks the three-level hierarchy before expanding;
missing parents, invalid levels or cycles fail as operational errors (SQLSTATE
55000), never a partial successful scope. Overlapping roots produce one final
category ID per leaf; evidence may legitimately contain several root paths.

## Queries, privilege and migration

The repository uses parameterized arrays: one batched candidate statement and
at most one batched expansion statement. It does not load the product table,
run per-leaf SQL or fetch all products into memory. Only selected metadata
candidates/scopes are hydrated. The current dataset has 2,003 category nodes;
metadata scans and hierarchy integrity checks are bounded by this catalog size,
not a hard-coded SQL row cap. No production latency/SLA claim is made here.

Use a client inside a repeatable-read, read-only transaction for a consistent
multi-statement resolution. The CLI demonstrates this. The future search
repository must use the same snapshot for category/filter/product decisions.
Errors from the database propagate; they must map to search_unavailable at the
HTTP boundary rather than empty products. Runtime cannot apply migrations.

Migration expects the existing pg_trgm extension in `extensions`; it fails if
that prerequisite is missing instead of installing/moving it. Existing Commit
10 grants USAGE on extensions. The new functions grant EXECUTE only to
catalog_search_reader; source privileges and published guards are unchanged.
No new table/index/cache/materialized view or seed. Applied 0001/0002 are immutable.
Down drops only the two new functions, preserving metadata and extensions.

`npm run db:check-categories` verifies real login identity and expected TV scope,
OLED/QLED, multi-category union and conservative typo/unknown behavior, in a
read-only snapshot. Run after 0003, plus db:check-access and db:check-metadata.
This is a CLI check, not a search endpoint.

## Tests and remaining work

Tests run real pg_trgm on PGlite PostgreSQL 18.3 and the real node-pg-migrate
runner. They cover exact/alias/ID, broad/leaf/accessories, OR union, ambiguity,
unknown and truncated candidates, parameter injection, deterministic ordering,
bounded SQL calls, hierarchy damage, access, replay and rollback. They do not
substitute for production PostgreSQL 18.6 smoke and later concurrency testing.

Commit 13: metadata-typed attribute/brand/feature/unit/operator resolution.
Commit 14–16: SQL product search, repository and authenticated Tool API.
Commit 20: category semantic evidence. Commit 24–25: evaluation/performance.

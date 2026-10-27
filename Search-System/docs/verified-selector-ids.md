# Optional verified IDs in Tool JSON — Commit 14

Additive request fields in version "1"; every old field remains valid. No new
HTTP route, product search/ranking, embeddings, product publication, table or seed.
The keyword SQL work moves to Commit 15; the keyword Tool API milestone to 17.

## Request fields

| Field | Meaning |
|---|---|
| filters.category_query | Existing category text/aliases/explicit OR list |
| filters.category_ids | Optional nonempty unique array, 1–20 uppercase category IDs |
| filters.feature_queries_all | Existing feature names; every feature is required |
| filters.feature_ids_all | Optional nonempty unique array, 1–20 FTxxxx IDs; every ID is required |
| filters.specs[N].attribute_query | Existing spec name/code/alias |
| filters.specs[N].attribute_id | Optional ATxxxx ID, instead of or alongside its name |

Each spec needs at least attribute_query OR attribute_id, with op/value and the
same typed unit rules as before. Both selectors are allowed. Numeric JSON stays
numeric, boolean false stays false, text is not guessed. ID-only constraints count
as nonempty searches. IDs are case-sensitive canonical codes: lowercase, numeric,
duplicate, empty/null arrays and unknown fields are rejected at runtime validation.
Brand queries stay as before; brand_ids is not introduced in this commit.

See examples/tool-search-v1/request-with-ids.json (the full previous JSON with
new fields) and request-ids-only.json (the same shape without redundant names).
version/query/semantic_query/price/page/limit/sort/exact fields keep their contracts.
semantic_query remains planned for the embedding/hybrid phase, not executed here.

## Consistency and mixed selectors

- Category IDs are verified against categories.id with no name/alias/fuzzy lookup.
  Parent IDs are allowed; expand only their real level-3 descendants. Overlapping
  parent/leaf scopes deduplicate. One unknown or empty root blocks the whole ID
  selector with needs_clarification, indexed /filters/category_ids/N diagnostics.
- With category_query AND category_ids, the expanded ID scope must be a subset
  of the resolved name scope. “ทีวี” + OLED ID narrows safely; “OLED” + QLED ID is
  constraint_conflict. Unknown/ambiguous names cannot be bypassed by supplied IDs.
- An attribute ID is looked up directly inside each selected leaf category.
  If attribute_query is also present, it must name that attribute (including a
  matching candidate in an ambiguous label). An ID/name mismatch is a branch
  conflict, never silently prefer one side. A known ID in another category makes
  that OR branch impossible; unknown ID or unusable explicit ID (wrong type,
  nonfilterable, unsupported unit) requires clarification instead of relaxing it.
- Feature IDs are verified against features.id and category_features. A known ID
  absent from a category makes that branch impossible. An unknown ID requires
  clarification; it is never ignored or rewritten via aliases.
- Feature name and ID ARRAYS are independent AND lists, not positional pairs.
  Their union must be required, deduplicating the same feature. Name HDMI + ID VRR
  means both HDMI and VRR, not that the lists contradict or are zipped by index.
  Every item must be valid in the branch. If names cannot be resolved alongside
  explicit IDs, require clarification rather than drop those names.
- The legacy name-only path is unchanged, including its existing partial-filter
  diagnostics. Stricter explicit-ID failures do not silently downgrade to names.
  Preserve status/unapplied/all_requested_filters_applied for later search/API code.

## Internal integration and SQL

Use resolveCategoryFilters(client, request.filters) as the new category entry:
without category_ids it delegates to Commit 12 unchanged. With IDs it calls the
new category_id_scope, and checks any additional name selector using the old
resolver. Returns internal CategoryResolution with optional selector_issues and
input_category_ids provenance. No ready category IDs are returned on conflict.

Only call typedFilterRepository.resolve(scope, filters) when scope.status is
resolved. Do not replace scope.category_ids with caller IDs or strip selectors
before passing the original filters. The adapter rejects mismatched provenance.
It calls old resolve_typed_filters for legacy input, and resolve_typed_filters_v2
when any new selector exists. v2 independently validates the JSON and rechecks
that expanded category_ids match the trusted server leaf scope.

Migration 0005 adds four SECURITY INVOKER helpers with empty search_path and
reader-only EXECUTE. Existing 0001–0004, helpers, grants/RLS, unit rules and values
are retained. Direct attribute and feature IDs use metadata joins/PK equality;
category IDs expand by parent IDs. No loading of the product catalog or new cache.

Use a REPEATABLE READ READ ONLY transaction client for category/filter/search
consistency. New code propagates DB failures; never use empty products on errors.
A clarification blocks search. A constraint-conflict branch is ineligible for
strict search when another OR branch remains usable. All branches contradictory
gives top-level constraint_conflict. Partial legacy filters must remain visible.

ID-only category + typed filters make two parameterized repository statements;
internal SQL performs its own validations. Tests also disable lexical name helpers
and verify the ID path still works. This demonstrates skipped name resolution,
not a measured end-to-end latency claim. Later Commit 26 measures performance.
Brands or names that are still supplied naturally still use their normal matching.

The numeric/unit/epsilon policy from Commit 13 is unchanged. Product search must
use declared conversion intervals, preserve unknowns and enforce published RLS.
Public Tool responses must still pass the existing strict v1 response validator;
these internal extra diagnostics are not a permission to return arbitrary fields.

## Deploy and rollback

Before new ID callers are enabled, deploy schema 0005 and saved code together.
Run typecheck/build/tests, inspect migration plan, then existing db:check/access/
metadata/categories/filters and the new db:check-selector-ids. Expect
selector_ids_ready. The script reads the two full JSON examples and performs
read-only smoke plus legacy/unknown/conflicting category checks.

Down removes only the four new functions; legacy named JSON still uses v1 helpers
unchanged. ID requests cannot work after rollback and must surface unavailable,
never automatically search by an inferred name. Leave 0001–0004 immutable and
roll back later dependent migrations first. Do not remove pinned checksums.

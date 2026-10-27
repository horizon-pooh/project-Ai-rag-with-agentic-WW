# PostgreSQL keyword product search — Commit 15

## Boundary and calling sequence

The internal SQL function is `catalog_search.search_products_keyword(text[], jsonb)`.
The first argument is the deduplicated leaf scope verified by `resolveCategoryFilters` from Commit 14.
The second is the validated Tool request, including the original human names or optional IDs.
Use parameters, never interpolate user text into SQL:

```ts
const parsed = parseToolSearchRequest(input);
if (!parsed.valid) { /* return validation errors */ }
else {
  const scope = await resolveCategoryFilters(client, parsed.value.filters);
  if (scope.status === "resolved") {
    const result = await client.query(
      "SELECT catalog_search.search_products_keyword($1::text[], $2::jsonb) AS result",
      [scope.category_ids, JSON.stringify(parsed.value)],
    );
  }
}
```

This is a partial calling example, not a new endpoint or the Commit 16 repository.
Stop on unresolved category scope. SQL independently validates the request and resolves typed filters again;
callers cannot supply fabricated resolved attribute/value objects. Name-based category selection is the
server resolver's responsibility; the scope argument must never come directly from an unverified HTTP field.
Use the same client and a read-only transaction when a consistent snapshot is needed across resolution and search.

The JSON returned here is `KeywordSearchEnvelope`, an INTERNAL result. It is not the public v1 response:
Commit 16 will batch-hydrate product facts and map diagnostics, and Commit 17 will expose authenticated routes.
There is no new all-products endpoint and no per-request product download to JavaScript.

## Keyword behavior

Search uses Article, model, Thai/English names, brand names/aliases, the category hierarchy and stored
`product_search_documents.keyword_text`. A literal whitespace token path supports Thai and substrings;
`simple` PostgreSQL full-text search over short core fields provides another lexical path.
No Thai word segmentation, semantic matching, embeddings or RRF is claimed.
The supplied `semantic_query` is unused and every result envelope reports `semantic_available: false`
and a `semantic_unavailable` warning. Semantic-only input asks for clarification rather than browsing everything.
Punctuation is literal text, not SQL or web-search syntax. A missing document can still match core fields.

## Filters and honest alternatives

Only published products in the selected leaf scope are candidates. Ready/partial filter branches are searched;
category branches are OR, brands within a branch are OR, and specs/features are AND.
Typed filter ambiguity prevents searching; conflicting branches are excluded, and an entirely conflicting
resolution returns `constraint_conflict`. Legacy unresolved requirements remain in the resolution diagnostics
with `all_requested_filters_applied: false`.

Numbers are compared in PostgreSQL numeric using the typed resolver's canonical unit and conversion epsilon.
Zero and boolean false are legitimate values. Missing, null, non-finite or wrongly typed specification values
are unknown, not zero/false. A missing positive product-feature link is `feature_not_recorded`, not proof of absence.
Known violations appear in `unmet_filters`; unavailable resolved facts appear in `unknown_filters`.
An alternative always has at least one of these differences. Unapplied legacy requirements stay at resolution
level rather than being presented as known product facts. `matches_applied_filters` claims only the conditions
that could actually be applied, never every sentence in the customer's request.

When there are too few exact matches, the remaining published products within the same selected categories
can fill the result as alternatives, including keyword mismatches explicitly marked at `/query`.
There is no silent expansion from OLED to QLED or another category outside the resolved scope.

Numeric difference values use canonical units, with `canonical_unit`, original `input_value`/`input_unit`
for specs, and decimal strings for lossless diagnostics. Public response mapping must preserve these meanings;
never display a converted numeric value with the original input unit.

## Identity lock

`exact.article_id` and `exact.model` are hard restrictions, never relaxed for alternatives.
A six-digit Article as the whole query is also locked. An exact visible model query and the catalog's
uppercase generated-model pattern are locked; an unknown model pattern returns no products instead of a
replacement model. For arbitrary model names use `exact.model` explicitly. Case-sensitive model identity is
retained. An identity conflict is diagnosed. A price/spec mismatch may still return the SAME requested Article
as an explained alternative; it never authorizes substitution of a different Article.

## Ranking and pagination

All strict matches precede all alternatives, including price and Article sorting.
For relevance: fewer distinct unmet/unknown conditions first, then fewer unknown conditions,
then lower worst normalized numeric violation, then higher lexical score, then Article ascending.
Numeric distance is `max(abs(actual-requested)/greatest(abs(actual),abs(requested),1))` across known numeric violations.
It is a ranking heuristic, not a calibrated quality score or a vector distance. Duplicate selectors retain
all original JSON Pointer diagnostics but do not multiply the same semantic difference in ranking.
Lexical score rewards exact model/Article and name matches plus literal/core full-text evidence.
Price ordering operates within strict/alternative tiers and uses Article to break ties.

Counts and global ordering are computed before pagination. `total_matches` and `total_alternatives` describe
the entire resolved search, `returned_count` describes this page, and `has_more` includes both tiers.
An exhausted page with positive global totals is `ok` with an empty array, not `no_matches`.
`no_matches` requires both totals to be zero. Results are bounded by the validated limit (maximum 100).
No arbitrary pre-ranking candidate cutoff is used.

## Access, migration and limits

Migration 0006 adds only two invoker functions, with empty search_path, PUBLIC execute revoked and
`catalog_search_reader` execute granted. Existing RLS and an explicit published predicate protect results;
no owner/security-definer bypass is introduced. The old `public.search_products` remains unchanged.
The migration does not change products, statuses, content, embedding configuration or indexes.
Down removes only the two new functions. Reapply/down/checksum behavior is tested on an isolated database.
Do not edit an applied migration's contents.

`db:check-keyword` uses the search login, read-only snapshot and small existing-category cases after migration.
It is a smoke check, not a full-catalog benchmark. Existing trigram indexes are not guaranteed to accelerate
computed concatenations or substring expressions. No latency, index-use or concurrency claim is made here;
measure query plans and p50/p95 against staging in Commit 26 before adding indexes.

Tests run with the real migration runner on isolated PostgreSQL 18.3/PGlite and fixture roles/RLS;
the production server reports PostgreSQL 18.6. The user must run real-server access and smoke checks after
applying migration. The additional 63-product snapshot check validates real data shapes, not full-catalog
relevance or performance. No production migration was applied while preparing this package.

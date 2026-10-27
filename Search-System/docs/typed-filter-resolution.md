# Typed filter resolution — Commit 13

Internal SQL resolution after Commit 12, before product search. This commit adds
no HTTP endpoint, product query/ranking, product write, publication, embeddings,
seed, table or dependency. Existing public search_products v1 is untouched.

## Integration

Use the same transaction-bound pg client for:

1. resolveCategoryQuery(categoryRepository, request.filters.category_query)
2. typedFilterRepository.resolve(resolvedCategoryScope, request.filters)
3. The future product search from Commit 14–16.

Use REPEATABLE READ READ ONLY to keep category/filter/product decisions on one
snapshot. A needs_clarification category result cannot proceed. The Tool caller
still provides human labels, not internal ID arrays or SQL. The repository checks
that category_query, if provided, matches the scope's original query, reuses
Commit 9 strict runtime validation (no coercion/mutation), and passes bounded
scope + JSON parameters to one SQL call. SQL independently validates its input.
Do not add an HTTP route that exposes raw helper functions or arbitrary scope IDs.

## Reused metadata

- Category-scoped category_attribute_search_catalog for attribute ID/code/name/
  text[] aliases, type, canonical unit/label, filterability and documented bounds.
- brands has name/name_th and JSONB aliases. Matching is global, not proof a
  brand currently has a published product in a selected category. Product search
  must apply both category and brand; zero inventory is an honest possible result.
- features has code/name_th/name_en and JSONB aliases, scoped by category_features.
- unit_conversion_rules supplies explicit direct conversions only.
- Existing RLS/metadata views and permissions remain active. Functions are
  SECURITY INVOKER with an empty search_path and reader-only EXECUTE.

No copied registry, guessed alias or unit rule is seeded. The read-only production
inspection found 1,088 brands, 7,145 feature definitions, 7,172 category-feature
links and 13,807 category-attribute links. These counts describe the snapshot,
not an assertion that every brand/feature is available in every product category.

## Meaning and diagnostics

SQL resolve_typed_filters returns an INTERNAL typed-filters-v1 result:

- OR between category branches and brand_ids. AND between resolved_specs and
  feature_ids_all inside a branch. IDs deduplicate, original binding paths remain.
- Specs include attribute_id, op, requested value/unit, canonical decimal or text/
  boolean value, unit evidence, precision and original `/filters/specs/N` path.
- Brands/features have resolved_brands/resolved_features bindings with original
  JSON Pointer positions. Never infer their positions from sorted/deduplicated IDs.
- Unknown, wrong type, nonfilterable, unsupported unit or absent-in-category
  constraints are unapplied with explicit reasons. Missing never becomes 0/false.
- Ambiguous spec/feature and unknown/ambiguous brand force needs_clarification.
  In particular, do not silently broaden an unknown brand to all brands or drop
  an unresolved OR brand choice. Candidate IDs/names are retained for confirmation.
- A branch may be partial when some spec/feature cannot apply. Its surviving
  constraints describe matches_applied_filters only. all_requested_filters_applied
  is false, including the top-level flag. A later API must return unapplied_filters
  and per-product unknown_filters, not claim all customer requirements were met.
- Conflicting applied numeric intervals or text/boolean equalities are reported
  per branch with every source path. All branches contradictory gives top-level
  constraint_conflict; an unsatisfiable branch must not participate in strict
  search if another OR branch remains usable. A clarification blocks search.
- Documented min/max are metadata/generation bounds, not a proved exhaustive
  inventory domain. Outside values are retained with outside_documented_range,
  not rewritten or automatically made no_matches. Actual inventory is checked later.

Text and boolean accept eq only. Text values are preserved exactly, including
numeric-looking strings when the metadata type is text. A string "55" targeting
number is data_type_mismatch, never 55. Canonical boolean false remains false.
A boolean request for a text field is also a mismatch, not a guessed description.
Product width and package width, wash capacity and dry capacity are distinct IDs.
The resolver does not pick the first ambiguous label or guess from similar words.
IDs shaped ATxxxx/BRxxxx/FTxxxx lock to that ID; aliases cannot redirect unknown IDs.

## Units and precision policy

Canonical values and prices use SQL numeric and decimal STRINGS internally to
avoid node-postgres/JSON floating-point loss. These internal strings do not change
Tool v1's input number requirements or permit silently coercing a caller string.
Commit 16 must deliberately map facts/diagnostics to the existing API contract.

- No supplied unit: use the attribute's canonical unit and mark assumed_canonical.
  A unitless attribute remains unitless. This assumption must stay visible.
- Exact canonical code or the attribute's own display label is valid even without
  a global rule, e.g. W/วัตต์, door/ประตู. Trim unit whitespace; retain symbol case.
- Other units need a direct enabled from/to rule. No transitive/fuzzy conversion
  and no kg↔L substitution. Disabled/unknown conversions are unapplied.
- Native/canonical values have comparison_epsilon = 0.
- For a converted value n, epsilon = max(abs(n)*1e-12, 1e-12), expressed in the
  canonical unit. This is an explicit finite-factor precision policy, not a
  customer tolerance, inferred measurement resolution or rounded stored value.
  Keep n unrounded and report its rule_version, requested input and unit mode.
- SQL product comparisons in Commit 14 MUST use: eq → [n-e,n+e], gte → >= n-e,
  lte → <= n+e. Conflict detection already uses these intervals. Do not compare
  converted eq with strict numeric equality or widen unconverted values.
- Example: 139.7 cm converts with the stored finite inverse factor to
  54.99999999999999956 inch. The declared epsilon prevents a false contradiction
  against an exact 55 inch request without rounding it behind the caller's back.

Price uses THB; zero is retained. Negative/inverted price and malformed JSON are
invalid requests at the input boundary. Empty/null bounds do not create a price
constraint. No currency conversion or price-based brand inference is performed.

## Operational contract

resolve_typed_filters accepts only 1–1,907 validated leaf IDs (the current catalog
limit), up to 20 specs, 20 brands and 20 features, bounded labels and allowed keys.
Catalog growth beyond this scope limit requires a reviewed new migration.
All metadata resolution happens in SQL; TypeScript validates/orchestrates and
formats internal results. It does not load products or perform product ranking.

Database errors propagate to the future search_unavailable boundary, never empty
results. The adapter makes one parameterized statement; PL/pgSQL loops over the
bounded metadata constraints/branches internally. Large scopes still have a cost;
no p95/concurrency claim is made here. Measure in Commit 25, do not add caching or
indexes without evidence. Keep consistent snapshots and existing query timeouts.

Migration 0004 adds four functions and grants only reader EXECUTE. It does not
modify 0001–0003, metadata, tables, products, mock flags or configuration. Down
removes only these helpers in dependency order. Prior category/metadata helpers
and extensions survive. Later dependent migrations must be reversed first.

After application run db:check/access/metadata/categories and db:check-filters.
Expected typed_filters_ready tests TV OLED + LG + HDMI + 55 inch + boolean false,
contradictory widths, a string/number mismatch, a real ambiguous drying-system
label in AP0106 and a millimeter-to-centimeter conversion. This is a read-only
CLI smoke, not a public search endpoint or evidence that a product exists.

References: PostgreSQL 18 official JSON functions and numeric documentation:
https://www.postgresql.org/docs/18/functions-json.html
https://www.postgresql.org/docs/18/datatype-numeric.html

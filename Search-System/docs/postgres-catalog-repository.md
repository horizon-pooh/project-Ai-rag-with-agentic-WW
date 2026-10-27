# PostgreSQL catalog repository — Commit 16

## Scope and use

`createPostgresCatalogRepository(database.pool)` provides `search(input)` and `findDetails(article)`.
Use the existing `createSearchDatabase` pool and call its identity check before serving requests.
The composition root owns pool shutdown; repositories neither create extra pools nor close a shared one.
No route, auth handler, new dependency, migration or database write is introduced here.
The old mock ProductRepository/SearchService remain untouched.

```ts
const database = createSearchDatabase(config);
await database.check();
const catalog = createPostgresCatalogRepository(database.pool);
const snapshot = await catalog.search({
  query: "ทีวี",
  filters: {category_ids: ["TV0108"], price: {max: 25000}},
  limit: 5,
});
const response = mapCatalogSearchResponse(snapshot, requestId);
const details = await catalog.findDetails(100346); // null for missing or invisible Article
// Application shutdown: await database.close().
```

`search` validates unknown input before acquiring a client, resolves category filters, calls SQL0006
using two parameters, and hydrates only the Articles on that result page. Category/filter clarification
is a normal domain outcome; database/timeout/data-corruption errors remain errors, never `no_matches`.
`CatalogInputError.issues` contains safe input diagnostics; `CatalogDataError` has a fixed message.
Do not serialize raw database errors. Commit17 will map failures to sanitized transport responses.

The repository deliberately uses the category-selection behavior from Commit14:
provide `filters.category_query` or `filters.category_ids`. A query-only or identity-only request
without a category scope currently asks for category clarification; this code does not invent a scope
or scan all products. The HTTP/service commit must decide and test any query-to-category or identity-to-category
policy explicitly. This limitation does not affect `findDetails(article)`.

## One transaction and bounded hydration

Every repository operation checks out one client, starts `REPEATABLE READ READ ONLY`, and uses it for
resolution/search/hydration. Success commits; failures roll back; release always runs. If rollback fails,
the client is discarded and the original error is preserved. Same-snapshot hydration prevents details
from reflecting a later price/status update while the search diagnostics describe the earlier version.
The caller's existing connection/query timeouts and pool maximum remain in force.

`createProductFactsRepository(client)` is a lower-level transaction-bound building block:

- `findMany(ids)` accepts at most100 integer Articles in100000–199999, deduplicates preserving first order,
  returns only published/visible matches, and uses no query for an empty input.
- Nonempty visible hydration uses three queries regardless of product count: products/brand/category,
  all specification values/definitions, and all positive product-feature links.
- Details add one content-view query for one Article. Search pages do not fetch full content sections.
- Specs/features/sections are grouped in memory AFTER bounded SQL reads. No findAll, per-product SQL loop,
  child-table Cartesian product, global catalog cache, or JavaScript filtering/ranking is added.
- SQL ranking and counts are preserved; hydration cannot silently remove missing facts or reorder the page.
  A mismatch within the same snapshot is a data error.

These are query-count properties, not a measured throughput or latency guarantee.

## Facts and details

Search facts match the existing ProductFacts contract. Include all recorded specs plus category definitions
with no recorded value as explicit null. Only product/category-matched values are read. Missing/null,
wrongly typed and non-finite specs remain null; boolean false and number zero remain real values.
Positive feature links are returned as evidence; absence is not interpreted as false.

All numeric columns are selected as text before JavaScript conversion. `decimalFact` accepts finite,
representable numbers, rejects unsafe integer rounding and nonzero underflow, and does not change pg's
global type parsers. Invalid mandatory price/currency or missing mock notice causes a data error.
Unknown optional specifications are null. JSON numbers use ordinary IEEE754 precision; full details
also preserve source numeric decimal strings. SQL matching still uses native PostgreSQL numeric.

Details contain ProductFacts plus name_en, series, short_description, regular_price, sale_unit,
pack_quantity, stock_quantity, typed specification metadata/decimal strings, feature operation/benefit/
limitation and the complete structured content view. Keep applicability and nested items unchanged;
never fabricate content for an empty or non-applicable section. Sections/specs/features have stable ordering.
Draft and absent Articles both return null, without revealing unpublished child data.

## Public v1 adapter

`mapCatalogSearchResponse(snapshot, requestId)` is pure and validated with the existing response validator.
It maps results, no-matches, clarification and conflict outcomes; adds no HTTP status/auth behavior.
It includes applied category/spec/brand/feature/price/explicit identity selectors and original JSON Pointer
paths. Categories/brands remain OR groups and features/specs remain AND within each category.
Read applied brand entries as alternatives in that category, not a demand for multiple brands simultaneously.
Unapplied/failed-branch requirements retain category IDs and a truthful global flag.

Public diagnostic values use canonical units. Because v1 diagnostic objects do not have a unit field,
the reason explicitly says `values in inch` (or another unit) and includes the original input unit/value
when converted. The internal SQL envelope retains exact decimal strings; public v1 does not add fields
that its strict schema rejects. Missing resolved facts remain actual_value:null and is_mock/mock_notice
remain visible. Internal scores/decimal metadata and arbitrary database rows are not spread into v1.
A malformed output or mismatched totals fails closed instead of silently adjusting results.

## Verification and next step

`npm run db:check-catalog` runs read-only checks through the actual search login after existing migration0006.
It exercises v1 mapping, search-page hydration, full details, known-draft exclusion and exhausted pagination.
Fixtures include independent roles/RLS and actual migrations. Mock pool failures verify rollback/release
behavior. Production smoke validates real data shapes; it is not full-catalog quality/concurrency evaluation.

Commit17 connects this repository and mapper to the service/controller, caller-token authentication,
request IDs, bounded routes and sanitized errors. It must explicitly handle product-not-found, invalid Article,
missing category scope, timeout and connection failure. It must not return a database outage as zero results.

References:
- [PostgreSQL18 transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html)
- [node-postgres transactions: same client](https://node-postgres.com/features/transactions)
- [node-postgres data types](https://node-postgres.com/features/types)

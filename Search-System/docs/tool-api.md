# Search Tool HTTP API — Commit 17

The API uses PostgreSQL keyword search, existing SQL resolvers and the bounded Commit16 repository.
It does not use Jev, embeddings, semantic category matching, vector ranking or a per-request full-catalog download.
Search JSON remains v1; both names and verified category/feature/attribute IDs are supported.

## Enable locally

From Search-System, keep the existing PostgreSQL .env and SSH tunnel. Run `npm run api:configure`:
it checks that .env is ignored by Git, generates a32-byte base64url caller token without printing it,
preserves an existing valid token and database settings, enables the API and sets private file permissions.
It fails if DATA_SOURCE is not postgres. Do not copy .env.example over the existing .env.

The environment additions are SEARCH_API_ENABLED=true and SEARCH_API_TOKEN=<private token>.
Default disabled mode retains health-only operation. Enabled mode refuses missing/invalid tokens or
missing PostgreSQL access; it does not fall back to mock data. Startup runs the existing search_api identity check.

Run `npm run dev` in one terminal, then `npm run api:check` in another. The check uses the private .env
and loopback SEARCH_API_BASE_URL (default http://127.0.0.1:3000), prints no token and performs only reads.
No migration or dependency installation is needed. `/health` is liveness, not a fresh per-request database probe.

## Calling contract

- `POST /api/search`, Content-Type application/json, Authorization: Bearer <caller token>.
- `GET /api/products/{article_id}` with the same Authorization header.
- `GET /health` remains public and returns only status/service.
- Request body limit64KiB; search limit maximum100; offset maximum10000, as in v1.
- Extra JSON fields and query-string parameters are rejected. Numbers/booleans are not coerced from strings.
- Article path is exactly six decimal digits100000–199999; a published or missing Article is read directly.
- There is no product-listing/export/write route. HEAD is not auto-exposed.
- Every request gets a server-generated UUID returned in X-Request-ID and response.request_id. Caller IDs are ignored.
- API responses use Cache-Control:no-store. Tokens/cookies/bodies are not included in request logs.

Bearer scheme is case-insensitive; token bytes remain case-sensitive. Token is a server-to-server secret,
kept in the friend's tool backend environment, not knowledge content or the AI prompt. The local check deliberately
accepts only loopback targets to avoid accidentally sending this secret to another host.
Local API binds127.0.0.1 by default. The friend's machine needs an authorized private connection/tunnel or the later
server deployment; this commit does not expose a public listener, change firewall rules or deploy remotely.

Example from a Node backend (token and network URL configured there):

```js
const response = await fetch(`${process.env.SEARCH_TOOL_BASE_URL}/api/search`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    authorization: `Bearer ${process.env.SEARCH_TOOL_TOKEN}`,
  },
  body: JSON.stringify({
    version: "1", query: "ทีวี", semantic_query: "ทีวีสำหรับดูหนัง",
    filters: {
      category_query: "ทีวี OLED", category_ids: ["TV0108"],
      feature_ids_all: ["FT0023"],
      price: {max: 25000, currency: "THB"},
      specs: [{attribute_id: "AT0048", op: "eq", value: 55, unit: "นิ้ว"}],
    }, page: 1, limit: 5, sort: "relevance",
  }),
  signal: AbortSignal.timeout(30000),
});
const result = await response.json();
// Inspect HTTP status AND result.status before answering the customer.
// Preserve mock notice, unapplied/unknown filters, alternatives and semantic warning.
```

## Category scope policy

Explicit filters.category_query/category_ids use the existing resolver unchanged; supplied IDs are checked,
not blindly trusted. If a non-identity query has no category selector, the service tries the WHOLE query as a
literal category label. For `ทีวี`, exact name/alias evidence can resolve a TV group. It does not rewrite a
conversation into guessed keywords. Ambiguous/similar/unrecognized text requests clarification.
Inferred category applied/clarification paths point to `/query`, and successful searches include
category_inferred_from_query. Original caller JSON is not mutated.

Semantic-only, filter-only without a category, and Article/model-only search without a category ask for scope.
Use GET details for an Article alone, or include a category with exact.article_id/exact.model for filtered search.
The generated-model/Article patterns are never guessed as category labels and never unlock replacement products.
This is an explicit bounded scope policy; it does not silently search all1907 types.

## Statuses

| HTTP | Body status | Meaning |
|---|---|---|
|200|ok|Products or an exhausted page; inspect totals/has_more|
|200|no_matches|Both global tier totals are known zero|
|200|needs_clarification|Need a category/filter choice; no fabricated results|
|400|invalid_request|Schema, type, range, path or JSON error|
|401|unauthorized|Missing/invalid caller token; checked before parsing body|
|404|not_found|Missing/invisible product or unknown route; drafts are indistinguishable from absent Articles|
|409|constraint_conflict|Selectors/typed constraints conflict|
|413|invalid_request|Body exceeds64KiB|
|415|invalid_request|Unsupported content type|
|503|search_unavailable|Database timeout/outage or facts/output cannot be validated|

Transport unauthorized/not_found errors have version/request_id/status/issues. Existing search response
variants are unchanged; the details response is `{version,request_id,status:"ok",details: CatalogProductDetails}`.
The OpenAPI3.1 artifact describes these routes; no unauthenticated documentation route is added.

`details` includes ProductFacts, typed decimal-preserving specs, feature descriptions and structured content
sections with applicability. Runtime validation precedes serialization, so invalid output is not silently
stripped into a believable successful response. Search output is checked with the existing v1 validator.
Every product is still mock data, and is_mock/mock_notice are retained.

A SQL/database error is503, never a successful empty result. Logs contain a bounded error code/request ID,
not raw SQL, connection URLs, headers or stack-bearing database errors. Pools and query timeouts remain as
configured in Commit10; requestTimeout controls HTTP request receipt, not an aggregate SQL workflow deadline.
No new whole-request cancellation or rate-limit claim is made. Private-network deployment and performance
measurement remain later work.

## Verification and next step

Tests include auth before JSON parse, duplicate headers, noncoercing validation, original JSON Pointer errors,
SQL/RLS/real repository flow, category fallback/provenance, identity lock, strict-before-alternatives,
full details, draft exclusion, output contract and sanitized database failures. Real HTTP smoke uses an
in-memory temporary token and live reader database; it does not modify private environment files or deploy.

Commit18 starts the separate embedding provider adapter with bounded retries; keyword API works before any
embedding backfill. Continue to preserve semantic_available=false until semantic retrieval is actually connected.

References:
- https://fastify.dev/docs/latest/Reference/Hooks/
- https://fastify.dev/docs/latest/Reference/Validation-and-Serialization/
- https://fastify.dev/docs/latest/Reference/Server/

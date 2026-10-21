import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { searchRequestSchema } from "../src/schemas/search-request.schema.js";
import { parseToolSearchRequest, TOOL_AJV_OPTIONS, validateToolSearchResponse } from "../src/validation/tool-search.validation.js";
import type { SearchResultsResponse, ToolSearchResult } from "../src/types/tool-search.types.js";

const validRequest = () => ({
  query: "ทีวี", semantic_query: "ทีวีสำหรับดูหนัง",
  filters: { category_query: "ทีวี OLED", price: { min: null, max: 25000 },
    specs: [{ attribute_query: "ขนาดหน้าจอ", op: "eq", value: 55, unit: "นิ้ว" }] },
  limit: 5,
});
const productResult = (id = 100000): ToolSearchResult => ({
  product: {
    article_id: id, name_th: "ทีวีตัวอย่างสำหรับทดสอบ", model: `TEST-${id}`,
    brand_id: "BR0001", brand_name: "TEST", category_id: "TV0108", category_name: "ทีวี OLED",
    price: 24900, currency: "THB", is_mock: true, mock_notice: "ข้อมูลจำลองสำหรับการศึกษา",
    specifications: [{ attribute_id: "AT0048", name: "ขนาดหน้าจอ", value: 55, unit: "inch" }],
    features: [],
  },
  match_type: "matches_applied_filters", unmet_filters: [], unknown_filters: [],
});
const response = (): SearchResultsResponse => ({
  version: "1", request_id: "test-1", status: "ok", query: "ทีวี",
  semantic_available: false, warnings: [{ code: "semantic_unavailable", message: "Keyword search only" }],
  resolved_categories: ["TV0108"], applied_filters: [], unapplied_filters: [],
  meta: { page: 1, limit: 5, returned_count: 1, total_matches: 1, total_alternatives: 0,
    has_more: false, all_requested_filters_applied: true }, results: [productResult()],
});

test("accepts agreed Thai request and copies defaults without mutating input", () => {
  const input = validRequest(); const before = structuredClone(input);
  const result = parseToolSearchRequest(input);
  assert.equal(result.valid, true);
  if (!result.valid) return;
  assert.equal(result.value.version, "1"); assert.equal(result.value.page, 1);
  assert.equal(result.value.sort, "relevance"); assert.equal(result.value.filters.price?.currency, "THB");
  assert.deepEqual(input, before);
  result.value.filters.specs![0].value = 65;
  assert.deepEqual(input, before);
});

for (const [name, input] of Object.entries({
  "filter-only": { filters: { category_query: "ทีวี" } },
  "price zero": { filters: { price: { max: 0 } } },
  "semantic-only": { semantic_query: "ดูหนังในห้องเล็ก" },
  "Article selector": { exact: { article_id: 100000 } },
  "model selector": { exact: { model: "TV0108-004" } },
  "decimal spec": { filters: { specs: [{ attribute_query: "ความจุซัก", op: "gte", value: 7.5, unit: "kg" }] } },
  "boolean false": { filters: { specs: [{ attribute_query: "อินเวอร์เตอร์", op: "eq", value: false }] } },
  "text equality": { filters: { specs: [{ attribute_query: "วัสดุ", op: "eq", value: "ไม้" }] } },
  "offset at boundary": { query: "ทีวี", page: 101, limit: 100 },
})) test(`accepts ${name}`, () => assert.equal(parseToolSearchRequest(input).valid, true));

for (const [name, input] of Object.entries({
  "null": null, "array": [], "empty": {}, "whitespace only": { query: " \n " },
  "empty filters": { filters: {} }, "null prices alone": { filters: { price: { min: null, max: null } } },
  "wrong version": { version: "2", query: "TV" },
  "unknown top-level field": { query: "TV", sql: "select 1" },
  "unknown nested field": { query: "TV", filters: { category_id: "TV0108" } },
  "unknown exact field": { exact: { model: "M", typo: true } },
  "empty exact": { exact: {} }, "string Article": { exact: { article_id: "100000" } },
  "out-of-range Article": { exact: { article_id: 99999 } },
  "string limit": { query: "TV", limit: "5" }, "zero page": { query: "TV", page: 0 },
  "fractional page": { query: "TV", page: 1.5 }, "zero limit": { query: "TV", limit: 0 },
  "limit too large": { query: "TV", limit: 101 }, "deep offset": { query: "TV", page: 102, limit: 100 },
  "negative price": { query: "TV", filters: { price: { max: -1 } } },
  "string price": { query: "TV", filters: { price: { max: "25000" } } },
  "inverted price": { query: "TV", filters: { price: { min: 100, max: 99 } } },
  "wrong currency": { query: "TV", filters: { price: { max: 50, currency: "USD" } } },
  "string numeric comparison": { query: "TV", filters: { specs: [{ attribute_query: "ขนาดจอ", op: "gte", value: "55" }] } },
  "boolean ordered comparison": { query: "TV", filters: { specs: [{ attribute_query: "อินเวอร์เตอร์", op: "gte", value: true }] } },
  "unit on boolean": { query: "TV", filters: { specs: [{ attribute_query: "อินเวอร์เตอร์", op: "eq", value: false, unit: "kg" }] } },
  "unit on text": { query: "TV", filters: { specs: [{ attribute_query: "วัสดุ", op: "eq", value: "ไม้", unit: "kg" }] } },
  "blank spec name": { query: "TV", filters: { specs: [{ attribute_query: " ", op: "eq", value: 55 }] } },
  "unknown spec field": { query: "TV", filters: { specs: [{ attribute_query: "จอ", op: "eq", value: 55, id: "AT0048" }] } },
  "unsupported operator": { query: "TV", filters: { specs: [{ attribute_query: "จอ", op: "like", value: 55 }] } },
  "too many specs": { query: "TV", filters: { specs: Array.from({ length: 21 }, () => ({ attribute_query: "จอ", op: "eq", value: 55 })) } },
  "too many features": { query: "TV", filters: { feature_queries_all: Array.from({ length: 21 }, (_, i) => `f${i}`) } },
  "query too long": { query: "ก".repeat(201) },
  "non-finite number": { query: "TV", filters: { price: { max: Infinity } } },
})) test(`rejects ${name}`, () => assert.equal(parseToolSearchRequest(input).valid, false));

test("numeric-looking text is preserved, not guessed without database metadata", () => {
  const result = parseToolSearchRequest({ filters: { specs: [{ attribute_query: "รหัสสี", op: "eq", value: "55" }] } });
  assert.equal(result.valid, true);
  if (result.valid) assert.equal(typeof result.value.filters.specs![0].value, "string");
});
test("boolean-looking text remains text until typed metadata resolution", () => {
  const result = parseToolSearchRequest({ filters: { specs: [{ attribute_query: "ชื่อโหมด", op: "eq", value: "true" }] } });
  assert.equal(result.valid, true);
  if (result.valid) assert.equal(result.value.filters.specs![0].value, "true");
});
test("unknown property errors identify their JSON Pointer without removing data", () => {
  const input = { query: "TV", filters: { category_id: "TV0108" } };
  const result = parseToolSearchRequest(input);
  assert.equal(result.valid, false);
  if (!result.valid) assert.ok(result.issues.some(i => i.path === "/filters/category_id"));
  assert.equal(input.filters.category_id, "TV0108");
});

test("accepts honest strict and alternative diagnostics", () => {
  const data = response(); const alt = productResult(100001); alt.product.price = 26900;
  alt.match_type = "alternative";
  alt.unmet_filters = [{ path: "/filters/price/max", requested_value: 25000, actual_value: 26900, reason: "above_budget" }];
  data.results.push(alt); data.meta.returned_count = 2; data.meta.total_alternatives = 1;
  assert.equal(validateToolSearchResponse(data).valid, true);
});
test("unknown spec remains null and may explain an alternative", () => {
  const data = response(); data.results[0].match_type = "alternative";
  data.results[0].unknown_filters = [{ path: "/filters/specs/0", requested_value: 55, actual_value: null, reason: "missing_spec" }];
  data.meta.total_matches = 0; data.meta.total_alternatives = 1;
  assert.equal(validateToolSearchResponse(data).valid, true);
});
test("unapplied filters are allowed only with a truthful flag", () => {
  const data = response();
  data.unapplied_filters.push({ category_id: "TV0108", path: "/filters/specs/0", reason: "ambiguous" });
  assert.equal(validateToolSearchResponse(data).valid, false);
  data.meta.all_requested_filters_applied = false;
  assert.equal(validateToolSearchResponse(data).valid, true);
});
test("supports known no matches, exhausted page and unknown totals separately", () => {
  const empty = response(); empty.status = "no_matches"; empty.results = [];
  empty.meta.returned_count = 0; empty.meta.total_matches = 0;
  assert.equal(validateToolSearchResponse(empty).valid, true);
  const page = response(); page.meta.page = 2; page.results = []; page.meta.returned_count = 0;
  assert.equal(validateToolSearchResponse(page).valid, true);
  const unknown = response(); unknown.meta.total_matches = null; unknown.meta.total_alternatives = null;
  assert.equal(validateToolSearchResponse(unknown).valid, true);
});
for (const status of ["invalid_request", "constraint_conflict", "search_unavailable"] as const) {
  test(`accepts ${status} error contract`, () => assert.equal(validateToolSearchResponse({
    version: "1", request_id: "r1", status, issues: [{ path: "/", code: status, message: "Safe public explanation" }],
  }).valid, true));
}
test("supports clarification without fabricated product results", () => {
  assert.equal(validateToolSearchResponse({ version: "1", request_id: "r1", status: "needs_clarification",
    clarifications: [{ path: "/filters/category_query", reason: "ambiguous", candidates: [{ id: "TV0108", label: "OLED" }] }],
  }).valid, true);
});

const responseFailures: Record<string, (data: SearchResultsResponse) => void> = {
  "wrong count": d => { d.meta.returned_count = 5; },
  "unknown response field": d => { Object.assign(d, { raw_vector: [1, 2] }); },
  "unknown product field": d => { Object.assign(d.results[0].product, { status: "draft" }); },
  "string Article": d => { Object.assign(d.results[0].product, { article_id: "100000" }); },
  "missing mock notice": d => { d.results[0].product.mock_notice = null; },
  "unexplained alternative": d => { d.results[0].match_type = "alternative"; },
  "strict with unmet filter": d => { d.results[0].unmet_filters = [{ path: "/filters/price/max", requested_value: 100, actual_value: 200, reason: "over" }]; },
  "unknown represented by zero": d => { d.results[0].match_type = "alternative"; d.results[0].unknown_filters = [{ path: "/filters/specs/0", requested_value: 55, actual_value: 0, reason: "missing" }]; },
  "missing semantic warning": d => { d.warnings = []; },
  "returned more than limit": d => { d.meta.limit = 1; d.results.push(productResult(100001)); d.meta.returned_count = 2; d.meta.total_matches = 2; },
  "duplicate Article": d => { d.results.push(productResult()); d.meta.returned_count = 2; d.meta.total_matches = 2; },
  "strict after alternative": d => {
    const alt = productResult(100001); alt.match_type = "alternative";
    alt.unmet_filters = [{ path: "/filters/price/max", requested_value: 100, actual_value: 200, reason: "over" }];
    d.results.unshift(alt); d.meta.returned_count = 2; d.meta.total_alternatives = 1;
  },
  "false global no matches": d => { d.status = "no_matches"; d.results = []; d.meta.returned_count = 0; },
  "empty page claiming later results": d => { d.results = []; d.meta.returned_count = 0; d.meta.has_more = true; },
};
for (const [name, mutate] of Object.entries(responseFailures)) {
  test(`rejects response: ${name}`, () => {
    const data = response(); mutate(data); assert.equal(validateToolSearchResponse(data).valid, false);
  });
}

test("Fastify uses the same strict schema/options without changing request JSON", async () => {
  const app = Fastify({ ajv: { customOptions: TOOL_AJV_OPTIONS } });
  // Test fixture route only. No application search endpoint is added in this commit.
  app.post("/contract-fixture", { schema: { body: searchRequestSchema } }, async (request, reply) => {
    const parsed = parseToolSearchRequest(request.body);
    return reply.code(parsed.valid ? 200 : 422).send(parsed);
  });
  try {
    assert.equal((await app.inject({ method: "POST", url: "/contract-fixture", payload: validRequest() })).statusCode, 200);
    assert.equal((await app.inject({ method: "POST", url: "/contract-fixture", payload: { query: "TV", limit: "5" } })).statusCode, 400);
    assert.equal((await app.inject({ method: "POST", url: "/contract-fixture", payload: { query: "TV", extra: true } })).statusCode, 400);
    assert.equal((await app.inject({ method: "POST", url: "/contract-fixture", payload: { query: "TV", filters: { price: { min: 2, max: 1 } } } })).statusCode, 422);
  } finally { await app.close(); }
});
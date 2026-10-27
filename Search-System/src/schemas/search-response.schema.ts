import { articleId, categoryId, nonBlankText, nullable, objectSchema, scalar, scalarOrList } from "./contract-schema.helpers.js";

const list = (items: ReturnType<typeof objectSchema>, minItems = 0) =>
({
    type: "array",
    items,
    minItems

});

const issue = objectSchema
    ({
        path: { type: "string" },
        code: nonBlankText(),
        message: nonBlankText(1000)
    });

const diagnostic = objectSchema
({
    path: nonBlankText(), requested_value: scalarOrList,
    actual_value: nullable(scalarOrList),
    reason: nonBlankText(1000),
});

const product = objectSchema
({
    article_id: articleId,
    name_th: nonBlankText(1000),
    model: nonBlankText(100),
    brand_id: { type: "string", pattern: "^BR[0-9]{4}$" },
    brand_name: nonBlankText(),
    category_id: categoryId,
    category_name: nonBlankText(),
    price: { type: "number", minimum: 0 },
    currency: { const: "THB" },
    is_mock: { type: "boolean" },
    mock_notice: nullable(nonBlankText(1000)),
    specifications: list(objectSchema
        ({
        attribute_id: { type: "string", pattern: "^AT[0-9]{4}$" },
        name: nonBlankText(), value: nullable(scalar),
        unit: nullable(nonBlankText(32)),
    })),
    features: list(objectSchema
        ({
        feature_id: { type: "string", pattern: "^FT[0-9]{4}$" },
        name: nonBlankText(),
    })),
});

const result = objectSchema
({
    product,
    match_type: { enum: ["matches_applied_filters", "alternative"] },
    unmet_filters: list(diagnostic),
    unknown_filters: list(diagnostic),
});

const base = { version: { const: "1" }, request_id: nonBlankText(100) };

const resultProperties = {
    ...base,
    query: { type: "string", maxLength: 200 },
    semantic_available: { type: "boolean" },
    resolved_categories: { type: "array",
    uniqueItems: true, items: categoryId },
    applied_filters: list(objectSchema({
        category_id: nullable(categoryId),
        path: nonBlankText(),
        resolved_id: nullable(nonBlankText()),
        op: { enum: ["eq", "gte", "lte", "in"] },
        value: scalarOrList,
        unit: nullable(nonBlankText(32)),
    })),
    unapplied_filters: list(objectSchema({
        category_id: nullable(categoryId),
        path: nonBlankText(),
        reason: nonBlankText(1000),
    })),
    warnings: list(objectSchema({
        code: nonBlankText(),
        message: nonBlankText(1000)
    })),
    meta: objectSchema({
        page: { type: "integer", minimum: 1, maximum: 10001 },
        limit: { type: "integer", minimum: 1, maximum: 100 },
        returned_count: { type: "integer", minimum: 0, maximum: 100 },
        total_matches: nullable({ type: "integer", minimum: 0 }),
        total_alternatives: nullable({ type: "integer", minimum: 0 }),
        has_more: { type: "boolean" },
        all_requested_filters_applied: { type: "boolean" },
    }),
};

export const searchResponseSchema = {
    $id: "urn:search-tool:response:1",
    oneOf: [
        objectSchema({ ...resultProperties, status: { const: "ok" }, results: { ...list(result), maxItems: 100 } }),
        objectSchema({ ...resultProperties, status: { const: "no_matches" }, results: { ...list(result), maxItems: 0 } }),
        objectSchema({
            ...base, status: { const: "needs_clarification" }, clarifications: list(objectSchema({
                path: nonBlankText(), reason: nonBlankText(1000),
                candidates: list(objectSchema({ id: nonBlankText(), label: nonBlankText() })),
            }), 1)
        }),
        objectSchema({ ...base, status: { enum: ["invalid_request", "constraint_conflict", "search_unavailable"] }, issues: list(issue, 1) }),
    ],
};

/** Reused by the details endpoint; the search v1 shape remains unchanged. */
export { product as productFactsSchema };

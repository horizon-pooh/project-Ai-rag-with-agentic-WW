import { articleId, nonBlankText, nullable, objectSchema } from "./contract-schema.helpers.js";

const attributeId = { type: "string", pattern: "^AT[0-9]{4}$" };
const selectorRequired = { anyOf: [
    { properties: { attribute_query: nonBlankText() }, required: ["attribute_query"] },
    { properties: { attribute_id: attributeId }, required: ["attribute_id"] },
] };

const numericSpec = { ...objectSchema({
    attribute_query: nonBlankText(),
    attribute_id: attributeId,
    op: { enum: ["eq", "gte", "lte"] },
    value: { type: "number" },
    unit: nonBlankText(32),
}, ["op", "value"]), ...selectorRequired };

const textSpec = { ...objectSchema({
    attribute_query: nonBlankText(), attribute_id: attributeId, op: { const: "eq" }, value: nonBlankText(),
}, ["op", "value"]), ...selectorRequired };
const booleanSpec = { ...objectSchema({
    attribute_query: nonBlankText(), attribute_id: attributeId, op: { const: "eq" }, value: { type: "boolean" },
}, ["op", "value"]), ...selectorRequired };
const terms = { type: "array", maxItems: 20, uniqueItems: true, items: nonBlankText() };

export const searchRequestSchema = {
    $id: "urn:search-tool:request:1",
    ...objectSchema({
        version: { const: "1" },
        query: { type: "string", maxLength: 200 },
        semantic_query: nonBlankText(2000),
        filters: objectSchema({
            category_query: nonBlankText(),
            category_ids: { type: "array", minItems: 1, maxItems: 20, uniqueItems: true,
                items: { type: "string", pattern: "^[A-Z]{2}[0-9]{4}$" } },
            brand_queries: terms,
            price: {
                ...objectSchema({
                    min: nullable({ type: "number", minimum: 0 }),
                    max: nullable({ type: "number", minimum: 0 }),
                    currency: { const: "THB" },
                }, []),
            },
            specs: {
                type: "array",
                maxItems: 20,
                items: { oneOf: [numericSpec, textSpec, booleanSpec] }
            },
            feature_queries_all: terms,
            feature_ids_all: { type: "array", minItems: 1, maxItems: 20, uniqueItems: true,
                items: { type: "string", pattern: "^FT[0-9]{4}$" } },
        }, []),
        exact: { ...objectSchema({ article_id: articleId, model: nonBlankText(100) }, []), minProperties: 1 },
        sort: { enum: ["relevance", "price_asc", "price_desc", "id"] },
        page: { type: "integer", minimum: 1, maximum: 10001 },
        limit: { type: "integer", minimum: 1, maximum: 100 },
    },  []),
};

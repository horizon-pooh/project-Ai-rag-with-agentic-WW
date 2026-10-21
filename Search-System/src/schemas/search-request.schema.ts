import { articleId, nonBlankText, nullable, objectSchema } from "./contract-schema.helpers.js";

const numericSpec = objectSchema({
    attribute_query: nonBlankText(),
    op: { enum: ["eq", "gte", "lte"] },
    value: { type: "number" },
    unit: nonBlankText(32),
}, ["attribute_query", "op", "value"]);

const textSpec = objectSchema({
    attribute_query: nonBlankText(), op: { const: "eq" }, value: nonBlankText(),
});
const booleanSpec = objectSchema({
    attribute_query: nonBlankText(), op: { const: "eq" }, value: { type: "boolean" },
});
const terms = { type: "array", maxItems: 20, uniqueItems: true, items: nonBlankText() };

export const searchRequestSchema = {
    $id: "urn:search-tool:request:1",
    ...objectSchema({
        version: { const: "1" },
        query: { type: "string", maxLength: 200 },
        semantic_query: nonBlankText(2000),
        filters: objectSchema({
            category_query: nonBlankText(),
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
        }, []),
        exact: { ...objectSchema({ article_id: articleId, model: nonBlankText(100) }, []), minProperties: 1 },
        sort: { enum: ["relevance", "price_asc", "price_desc", "id"] },
        page: { type: "integer", minimum: 1, maximum: 10001 },
        limit: { type: "integer", minimum: 1, maximum: 100 },
    },  []),
};

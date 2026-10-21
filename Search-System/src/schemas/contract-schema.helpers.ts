import type { AnySchemaObject } from "ajv";

export function objectSchema(
    properties: Record<string, AnySchemaObject>,
    required: string[] = Object.keys(properties),
): AnySchemaObject {
    return { type: "object", additionalProperties: false, properties, required };
}

export const nonBlankText = (maxLength = 200): AnySchemaObject =>
    ({ type: "string", minLength: 1, maxLength, pattern: "\\S" });

export const nullable = (schema: AnySchemaObject): AnySchemaObject =>
    ({ anyOf: [schema, { type: "null" }] });

export const scalar: AnySchemaObject = {
    anyOf:
    [
        { type: "string", maxLength: 2000 },
        { type: "number" },
        { type: "boolean" }
    ],
};

export const scalarOrList: AnySchemaObject =
{
    anyOf:
        [scalar, {
            type: "array",
            minItems: 1,
            maxItems: 20,
            items: scalar
        }],
};

export const categoryId: AnySchemaObject =
{
    type: "string",
    pattern: "^[A-Z]{2}[0-9]{4}$"

};

export const articleId: AnySchemaObject =
{
    type: "integer",
    minimum: 100000,
    maximum: 199999
};

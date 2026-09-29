import type { Product } from "../../types/product.types.js";

import type {
  SearchFieldName,
} from "../../config/search-fields.config.js";

export interface SearchableField {
  field: SearchFieldName;
  values: string[];
}

function attributeValues(
  product: Product
): string[] {
  return Object.values(
    product.attributes
  ).map((value) => String(value));
}

export function getSearchableFields(
  product: Product
): SearchableField[] {
  return [
    {
      field: "name",
      values: [product.name],
    },

    {
      field: "aliases",
      values: product.aliases,
    },

    {
      field: "tags",
      values: product.tags,
    },

    {
      field: "brand",
      values: product.brand
        ? [product.brand]
        : [],
    },

    {
      field: "category",
      values: product.category
        ? [product.category]
        : [],
    },

    {
      field: "description",
      values: product.description
        ? [product.description]
        : [],
    },

    {
      field: "searchableText",
      values: product.searchableText
        ? [product.searchableText]
        : [],
    },

    {
      field: "attributes",
      values: attributeValues(product),
    },
  ];
}
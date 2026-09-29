import { normalizeQuery } from "../processing/index.js";

import type {
  Product,
  ProductAttributeValue,
} from "../../types/product.types.js";

function valuesMatch(
  productValue: ProductAttributeValue,
  filterValue: ProductAttributeValue
): boolean {
  if (
    typeof productValue === "string" &&
    typeof filterValue === "string"
  ) {
    return (
      normalizeQuery(productValue) ===
      normalizeQuery(filterValue)
    );
  }

  return productValue === filterValue;
}

export function filterByAttributes(
  products: Product[],
  attributes?: Record<
    string,
    ProductAttributeValue
  >
): Product[] {
  if (
    !attributes ||
    Object.keys(attributes).length === 0
  ) {
    return products;
  }

  return products.filter((product) =>
    Object.entries(attributes).every(
      ([key, expectedValue]) => {
        const actualValue =
          product.attributes[key];

        if (actualValue === undefined) {
          return false;
        }

        return valuesMatch(
          actualValue,
          expectedValue
        );
      }
    )
  );
}
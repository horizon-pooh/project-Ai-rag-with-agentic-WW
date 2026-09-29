import { normalizeQuery } from "../processing/index.js";

import type { Product } from "../../types/product.types.js";

export function filterByCategories(
  products: Product[],
  categories?: string[]
): Product[] {
  if (!categories || categories.length === 0) {
    return products;
  }

  const normalizedCategories =
    new Set(
      categories.map((category) =>
        normalizeQuery(category)
      )
    );

  return products.filter((product) => {
    if (!product.category) {
      return false;
    }

    return normalizedCategories.has(
      normalizeQuery(product.category)
    );
  });
}
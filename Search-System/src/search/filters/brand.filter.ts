import { normalizeQuery } from "../processing/index.js";

import type { Product } from "../../types/product.types.js";

export function filterByBrands(
  products: Product[],
  brands?: string[]
): Product[] {
  if (!brands || brands.length === 0) {
    return products;
  }

  const normalizedBrands =
    new Set(
      brands.map((brand) =>
        normalizeQuery(brand)
      )
    );

  return products.filter((product) => {
    if (!product.brand) {
      return false;
    }

    return normalizedBrands.has(
      normalizeQuery(product.brand)
    );
  });
}
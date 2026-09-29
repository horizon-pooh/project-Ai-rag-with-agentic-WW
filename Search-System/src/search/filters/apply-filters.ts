import { filterByAttributes } from "./attribute.filter.js";
import { filterByBrands } from "./brand.filter.js";
import { filterByCategories } from "./category.filter.js";

import type { Product } from "../../types/product.types.js";
import type { SearchFilters } from "../../types/filter.types.js";

export function applyFilters(
  products: Product[],
  filters?: SearchFilters
): Product[] {
  if (!filters) {
    return products;
  }

  let filtered = products;

  filtered = filterByCategories(
    filtered,
    filters.categories
  );

  filtered = filterByBrands(
    filtered,
    filters.brands
  );

  filtered = filterByAttributes(
    filtered,
    filters.attributes
  );

  return filtered;
}
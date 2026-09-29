import type { Product } from "../../types/product.types.js";

import type {
  SearchFieldName,
} from "../../config/search-fields.config.js";

export interface StrategyResult {
  productId: string;

  score: number;

  matchedFields: SearchFieldName[];

  matchedTerms: string[];
}

export interface SearchStrategy {
  search(
    query: string,
    products: Product[]
  ): StrategyResult[];
}
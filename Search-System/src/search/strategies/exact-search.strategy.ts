import {
  SEARCH_FIELD_WEIGHTS,
} from "../../config/search-fields.config.js";

import {
  normalizeQuery,
} from "../processing/index.js";

import {
  getSearchableFields,
} from "./search-fields.js";

import type { Product } from "../../types/product.types.js";

import type {
  SearchStrategy,
  StrategyResult,
} from "./search-strategy.types.js";

export class ExactSearchStrategy
  implements SearchStrategy
{
  search(
    query: string,
    products: Product[]
  ): StrategyResult[] {
    const normalizedQuery =
      normalizeQuery(query);

    if (!normalizedQuery) {
      return [];
    }

    const results: StrategyResult[] = [];

    for (const product of products) {
      const fields =
        getSearchableFields(product);

      let bestScore = 0;

      const matchedFields =
        new Set<
          StrategyResult["matchedFields"][number]
        >();

      const matchedTerms =
        new Set<string>();

      for (const field of fields) {
        for (const value of field.values) {
          const normalizedValue =
            normalizeQuery(value);

          if (
            normalizedValue ===
            normalizedQuery
          ) {
            matchedFields.add(
              field.field
            );

            matchedTerms.add(
              normalizedQuery
            );

            bestScore = Math.max(
              bestScore,
              SEARCH_FIELD_WEIGHTS[
                field.field
              ]
            );
          }
        }
      }

      if (bestScore > 0) {
        results.push({
          productId: product.id,

          score: bestScore,

          matchedFields: [
            ...matchedFields,
          ],

          matchedTerms: [
            ...matchedTerms,
          ],
        });
      }
    }

    return results.sort(
      (a, b) =>
        b.score - a.score
    );
  }
}
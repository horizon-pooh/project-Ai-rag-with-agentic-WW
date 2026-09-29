import {
  SEARCH_FIELD_WEIGHTS,
} from "../../config/search-fields.config.js";

import {
  SEARCH_CONFIG,
} from "../../config/search.config.js";

import {
  normalizeQuery,
  tokenizeQuery,
} from "../processing/index.js";

import {
  getSearchableFields,
} from "./search-fields.js";

import {
  stringSimilarity,
} from "./string-similarity.js";

import type { Product } from "../../types/product.types.js";

import type {
  SearchStrategy,
  StrategyResult,
} from "./search-strategy.types.js";

export class FuzzySearchStrategy
  implements SearchStrategy
{
  search(
    query: string,
    products: Product[]
  ): StrategyResult[] {
    const queryTokens =
      tokenizeQuery(query);

    if (queryTokens.length === 0) {
      return [];
    }

    const results: StrategyResult[] = [];

    for (const product of products) {
      const fields =
        getSearchableFields(product);

      const matchedFields =
        new Set<
          StrategyResult["matchedFields"][number]
        >();

      const matchedTerms =
        new Set<string>();

      let totalScore = 0;

      for (const queryToken of queryTokens) {
        let bestTokenScore = 0;

        for (const field of fields) {
          for (const value of field.values) {
            const valueTokens =
              tokenizeQuery(
                normalizeQuery(value)
              );

            for (
              const valueToken of valueTokens
            ) {
              const similarity =
                stringSimilarity(
                  queryToken,
                  valueToken
                );

              if (
                similarity <
                SEARCH_CONFIG
                  .fuzzyMinSimilarity
              ) {
                continue;
              }

              const weightedScore =
                similarity *
                SEARCH_FIELD_WEIGHTS[
                  field.field
                ];

              if (
                weightedScore >
                bestTokenScore
              ) {
                bestTokenScore =
                  weightedScore;
              }

              matchedFields.add(
                field.field
              );

              matchedTerms.add(
                queryToken
              );
            }
          }
        }

        totalScore += bestTokenScore;
      }

      const score =
        totalScore /
        queryTokens.length;

      if (score > 0) {
        results.push({
          productId: product.id,
          score,
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
      (a, b) => b.score - a.score
    );
  }
}
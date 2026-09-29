import {
  buildFieldScores,
} from "./field-score.js";

import {
  calculateFinalScore,
} from "./final-score.js";

import type {
  StrategyResult,
} from "../strategies/index.js";

import type {
  RankedSearchResult,
  SearchStrategyScores,
} from "../../types/ranking.types.js";

interface StrategyResults {
  exact: StrategyResult[];
  keyword: StrategyResult[];
  fuzzy: StrategyResult[];
}

interface ProductAccumulator {
  scores: SearchStrategyScores;
  matchedFields:
    Set<StrategyResult["matchedFields"][number]>;
  matchedTerms: Set<string>;
}

export function mergeStrategyResults(
  results: StrategyResults
): RankedSearchResult[] {
  const products =
    new Map<
      string,
      ProductAccumulator
    >();

  const ensureProduct = (
    productId: string
  ): ProductAccumulator => {
    const existing =
      products.get(productId);

    if (existing) {
      return existing;
    }

    const created: ProductAccumulator = {
      scores: {
        exact: 0,
        keyword: 0,
        fuzzy: 0,
        semantic: 0,
      },

      matchedFields: new Set(),

      matchedTerms: new Set(),
    };

    products.set(
      productId,
      created
    );

    return created;
  };

  for (const result of results.exact) {
    const product =
      ensureProduct(result.productId);

    product.scores.exact =
      result.score;

    result.matchedFields.forEach(
      (field) =>
        product.matchedFields.add(
          field
        )
    );

    result.matchedTerms.forEach(
      (term) =>
        product.matchedTerms.add(term)
    );
  }

  for (const result of results.keyword) {
    const product =
      ensureProduct(result.productId);

    product.scores.keyword =
      result.score;

    result.matchedFields.forEach(
      (field) =>
        product.matchedFields.add(
          field
        )
    );

    result.matchedTerms.forEach(
      (term) =>
        product.matchedTerms.add(term)
    );
  }

  for (const result of results.fuzzy) {
    const product =
      ensureProduct(result.productId);

    product.scores.fuzzy =
      result.score;

    result.matchedFields.forEach(
      (field) =>
        product.matchedFields.add(
          field
        )
    );

    result.matchedTerms.forEach(
      (term) =>
        product.matchedTerms.add(term)
    );
  }

  const rankedResults: RankedSearchResult[] =
    [];

  for (
    const [
      productId,
      product,
    ] of products
  ) {
    const matchedFields = [
      ...product.matchedFields,
    ];

    rankedResults.push({
      productId,

      ranking: {
        strategies: product.scores,

        fields:
          buildFieldScores(
            matchedFields
          ),

        final:
          calculateFinalScore(
            product.scores
          ),
      },

      matchedFields,

      matchedTerms: [
        ...product.matchedTerms,
      ],
    });
  }

  return rankedResults.sort(
    (a, b) =>
      b.ranking.final -
      a.ranking.final
  );
}
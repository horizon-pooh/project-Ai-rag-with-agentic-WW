import {
  SEARCH_CONFIG,
} from "../config/search.config.js";

import {
  applyFilters,
} from "../search/filters/index.js";

import {
  paginate,
} from "../search/pagination/paginate.js";

import {
  mergeStrategyResults,
} from "../search/ranking/index.js";

import {
  ExactSearchStrategy,
  FuzzySearchStrategy,
  KeywordSearchStrategy,
} from "../search/strategies/index.js";

import type {
  ProductRepository,
} from "../repositories/index.js";

import type {
  SearchRequest,
  SearchResponse,
  SearchResult,
} from "../types/search.types.js";

export class SearchService {
  private readonly exactSearch =
    new ExactSearchStrategy();

  private readonly keywordSearch =
    new KeywordSearchStrategy();

  private readonly fuzzySearch =
    new FuzzySearchStrategy();

  constructor(
    private readonly productRepository:
      ProductRepository
  ) {}

  async search(
    request: SearchRequest
  ): Promise<SearchResponse> {
    const query = request.query.trim();

    if (!query) {
      return {
        success: true,
        query,

        meta: {
          total: 0,
          page: 1,
          limit:
            request.limit ??
            SEARCH_CONFIG.defaultLimit,
        },

        results: [],
      };
    }

    const allProducts =
      await this.productRepository.findAll();

    const candidateProducts =
      applyFilters(
        allProducts,
        request.filters
      );

    const exactResults =
      this.exactSearch.search(
        query,
        candidateProducts
      );

    const keywordResults =
      this.keywordSearch.search(
        query,
        candidateProducts
      );

    const fuzzyResults =
      this.fuzzySearch.search(
        query,
        candidateProducts
      );

    const rankedResults =
      mergeStrategyResults({
        exact: exactResults,
        keyword: keywordResults,
        fuzzy: fuzzyResults,
      });

    const productsById =
      new Map(
        candidateProducts.map(
          (product) => [
            product.id,
            product,
          ]
        )
      );

    const results: SearchResult[] =
      rankedResults
        .filter(
          (result) =>
            result.ranking.final >=
            SEARCH_CONFIG.minScore
        )
        .flatMap((result) => {
          const product =
            productsById.get(
              result.productId
            );

          if (!product) {
            return [];
          }

          const searchResult: SearchResult = {
            product,
            score:
              result.ranking.final,
          };

          if (request.debug) {
            searchResult.ranking =
              result.ranking;

            searchResult.matchedFields =
              result.matchedFields;

            searchResult.matchedTerms =
              result.matchedTerms;
          }

          return [searchResult];
        });

    const paginated =
      paginate(
        results,
        request.page,
        request.limit
      );

    return {
      success: true,
      query,

      meta: {
        total: paginated.total,
        page: paginated.page,
        limit: paginated.limit,
      },

      results: paginated.items,
    };
  }
}
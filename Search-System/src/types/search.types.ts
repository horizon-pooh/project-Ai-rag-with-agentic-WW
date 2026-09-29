import type { Product } from "./product.types.js";
import type { SearchFilters } from "./filter.types.js";
import type { RankingScore } from "./ranking.types.js";

export interface SearchRequest {
  query: string;

  filters?: SearchFilters;

  page?: number;
  limit?: number;

  debug?: boolean;
}

export interface SearchResult {
  product: Product;

  score: number;

  ranking?: RankingScore;

  matchedFields?: string[];
}

export interface SearchMeta {
  total: number;
  page: number;
  limit: number;
}

export interface SearchResponse {
  success: boolean;

  query: string;

  meta: SearchMeta;

  results: SearchResult[];
}
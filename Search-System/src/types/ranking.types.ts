import type {
  SearchFieldName,
} from "../config/search-fields.config.js";

export interface SearchStrategyScores {
  exact: number;
  keyword: number;
  fuzzy: number;
  semantic: number;
}

export interface FieldScores {
  name?: number;
  description?: number;
  brand?: number;
  category?: number;
  tags?: number;
  aliases?: number;
  attributes?: number;
  searchableText?: number;
}

export interface RankingScore {
  strategies: SearchStrategyScores;
  fields: FieldScores;
  final: number;
}

export interface RankedSearchResult {
  productId: string;

  ranking: RankingScore;

  matchedFields: SearchFieldName[];
  matchedTerms: string[];
}
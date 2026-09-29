import {
  SEARCH_FIELD_WEIGHTS,
} from "../../config/search-fields.config.js";

import type {
  SearchFieldName,
} from "../../config/search-fields.config.js";

import type {
  FieldScores,
} from "../../types/ranking.types.js";

export function buildFieldScores(
  fields: SearchFieldName[]
): FieldScores {
  const scores: FieldScores = {};

  for (const field of fields) {
    scores[field] =
      SEARCH_FIELD_WEIGHTS[field];
  }

  return scores;
}
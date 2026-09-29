import {
  RANKING_WEIGHTS,
} from "../../config/ranking.config.js";

import type {
  SearchStrategyScores,
} from "../../types/ranking.types.js";

export function calculateFinalScore(
  scores: SearchStrategyScores
): number {
  return (
    scores.exact *
      RANKING_WEIGHTS.exact +

    scores.keyword *
      RANKING_WEIGHTS.keyword +

    scores.fuzzy *
      RANKING_WEIGHTS.fuzzy +

    scores.semantic *
      RANKING_WEIGHTS.semantic
  );
}
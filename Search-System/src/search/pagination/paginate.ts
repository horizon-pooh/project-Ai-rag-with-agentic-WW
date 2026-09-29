import {
  SEARCH_CONFIG,
} from "../../config/search.config.js";

export interface PaginationResult<T> {
  items: T[];
  page: number;
  limit: number;
  total: number;
}

function normalizePositiveInteger(
  value: number | undefined,
  fallback: number
): number {
  if (
    value === undefined ||
    !Number.isInteger(value) ||
    value <= 0
  ) {
    return fallback;
  }

  return value;
}

export function paginate<T>(
  items: T[],
  requestedPage?: number,
  requestedLimit?: number
): PaginationResult<T> {
  const page =
    normalizePositiveInteger(
      requestedPage,
      SEARCH_CONFIG.defaultPage
    );

  const rawLimit =
    normalizePositiveInteger(
      requestedLimit,
      SEARCH_CONFIG.defaultLimit
    );

  const limit = Math.min(
    rawLimit,
    SEARCH_CONFIG.maxLimit
  );

  const start =
    (page - 1) * limit;

  return {
    items: items.slice(
      start,
      start + limit
    ),

    page,
    limit,
    total: items.length,
  };
}
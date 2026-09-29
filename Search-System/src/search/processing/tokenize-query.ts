import { normalizeQuery } from "./normalize-query.js";

export function tokenizeQuery(
  query: string
): string[] {
  const normalized = normalizeQuery(query);

  if (!normalized) {
    return [];
  }

  return normalized
    .split(" ")
    .filter(Boolean);
}
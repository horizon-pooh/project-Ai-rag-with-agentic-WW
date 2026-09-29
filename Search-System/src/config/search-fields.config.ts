export const SEARCH_FIELD_WEIGHTS = {
  name: 1.0,
  aliases: 0.9,
  tags: 0.8,
  brand: 0.7,
  category: 0.7,
  description: 0.5,
  searchableText: 0.4,
  attributes: 0.4,
} as const;

export type SearchFieldName =
  keyof typeof SEARCH_FIELD_WEIGHTS;
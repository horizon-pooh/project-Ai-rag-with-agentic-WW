import type {
  ProductAttributeValue,
} from "./product.types.js";

export interface SearchFilters {
  categories?: string[];
  brands?: string[];

  attributes?: Record<
    string,
    ProductAttributeValue
  >;
}
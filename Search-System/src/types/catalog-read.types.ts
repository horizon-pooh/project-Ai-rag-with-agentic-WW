import type {Pool, PoolClient} from "pg";
import type {CategoryResolution} from "./category-resolution.types.js";
import type {KeywordSearchEnvelope} from "./keyword-search.types.js";
import type {ProductFacts} from "./product-facts.types.js";
import type {ContractIssue, NormalizedToolSearchRequest} from "./tool-search.types.js";

export type CatalogClient = Pick<PoolClient, "query">;
export type CatalogPool = Pick<Pool, "connect">;
export type JsonValue = null | boolean | number | string | JsonValue[] | {[key: string]: JsonValue};
export interface CatalogContentSection {
  section_type: string;
  title: string;
  applicability: string;
  items: JsonValue;
  display_order: number;
}
export interface CatalogProductDetails {
  product: ProductFacts;
  name_en: string | null;
  series: string | null;
  short_description: string;
  regular_price: number | null;
  sale_unit: string;
  pack_quantity: number;
  stock_quantity: number;
  specifications: Array<ProductFacts["specifications"][number] & {
    data_type: "number" | "text" | "boolean";
    numeric_decimal: string | null;
    is_required: boolean;
  }>;
  feature_details: Array<{feature_id: string; name: string; detail: string; benefit: string; limitation: string}>;
  content_sections: CatalogContentSection[];
}
export interface CatalogSearchSnapshot {
  request: NormalizedToolSearchRequest;
  category_resolution: CategoryResolution;
  search: KeywordSearchEnvelope | null;
  products: ProductFacts[];
}
export class CatalogInputError extends Error {
  readonly code = "INVALID_CATALOG_INPUT";
  constructor(readonly issues: ContractIssue[]) { super("Invalid catalog request"); }
}
/** Intentionally contains no SQL, credentials or raw database errors. */
export class CatalogDataError extends Error {
  readonly code = "CATALOG_DATA_INVALID";
  constructor() { super("Catalog data cannot satisfy the response contract"); }
}

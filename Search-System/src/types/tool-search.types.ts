import type { FactValue, ProductFacts } from "./product-facts.types.js";

type AttributeSelector =
  | { attribute_query: string; attribute_id?: string }
  | { attribute_id: string; attribute_query?: string };
export type SpecConstraint = AttributeSelector & (
  | { op: "eq" | "gte" | "lte"; value: number; unit?: string }
  | { op: "eq"; value: string | boolean }
);

export interface ToolSearchFilters {
  category_query?: string;
  category_ids?: string[];
  brand_queries?: string[];
  price?: { min?: number | null; max?: number | null; currency?: "THB" };
  specs?: SpecConstraint[];
  feature_queries_all?: string[];
  feature_ids_all?: string[];
}

export interface ToolSearchRequest {
  version?: "1";
  query?: string;
  semantic_query?: string;
  filters?: ToolSearchFilters;
  exact?: { article_id?: number; model?: string };
  sort?: "relevance" | "price_asc" | "price_desc" | "id";
  page?: number;
  limit?: number;
}

export interface NormalizedToolSearchRequest extends ToolSearchRequest {
  version: "1";
  query: string;
  filters: ToolSearchFilters;
  sort: NonNullable<ToolSearchRequest["sort"]>;
  page: number;
  limit: number;
}

export interface ContractIssue { path: string; code: string; message: string }
export type ValidationResult<T> =
  | { valid: true; value: T }
  | { valid: false; issues: ContractIssue[] };

export interface AppliedFilter {
  category_id: string | null;
  path: string;
  resolved_id: string | null;
  op: "eq" | "gte" | "lte" | "in";
  value: FactValue | FactValue[];
  unit: string | null;
}

export interface UnappliedFilter {
  category_id: string | null;
  path: string;
  reason: string;
}

export interface ProductFilterDiagnostic {
  path: string;
  requested_value: FactValue | FactValue[];
  actual_value: FactValue | FactValue[] | null;
  reason: string;
}

export interface ToolSearchResult {
  product: ProductFacts;
  match_type: "matches_applied_filters" | "alternative";
  unmet_filters: ProductFilterDiagnostic[];
  unknown_filters: ProductFilterDiagnostic[];
}

interface ResponseBase { version: "1"; request_id: string }

export interface SearchResultsResponse extends ResponseBase {
  status: "ok" | "no_matches";
  query: string;
  semantic_available: boolean;
  resolved_categories: string[];
  applied_filters: AppliedFilter[];
  unapplied_filters: UnappliedFilter[];
  warnings: Array<{ code: string; message: string }>;
  meta: {
    page: number;
    limit: number;
    returned_count: number;
    total_matches: number | null;
    total_alternatives: number | null;
    has_more: boolean;
    all_requested_filters_applied: boolean;
  };
  results: ToolSearchResult[];
}

export interface ClarificationResponse extends ResponseBase {
  status: "needs_clarification";
  clarifications: Array<{
    path: string;
    reason: string;
    candidates: Array<{ id: string; label: string }>;
  }>;
}

export interface SearchErrorResponse extends ResponseBase {
  status: "invalid_request" | "constraint_conflict" | "search_unavailable";
  issues: ContractIssue[];
}

export type ToolSearchResponse = SearchResultsResponse | ClarificationResponse | SearchErrorResponse;

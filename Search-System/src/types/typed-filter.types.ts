export interface FilterDiagnostic {
  path: string;
  reason: string;
  candidates: Array<{id: string; label: string}>;
}
/** Canonical decimals remain strings internally; they are not public API value coercions. */
export interface ResolvedSpec {
  status: "applied";
  path: string;
  attribute_id: string;
  data_type: "number" | "text" | "boolean";
  op: "eq" | "gte" | "lte";
  numeric_value: string | null;
  text_value: string | null;
  boolean_value: boolean | null;
  unit_code: string | null;
  unit_mode: "unitless" | "assumed_canonical" | "canonical_or_label" | "converted" | "unchanged";
  rule_version: string | null;
  comparison_epsilon: string;
  requested_value: number | string | boolean;
  requested_unit: string | null;
  outside_documented_range: boolean;
}
export interface TypedFilterBranch {
  category_id: string;
  status: "ready" | "partial" | "needs_clarification" | "constraint_conflict";
  brand_ids: string[];
  resolved_brands: Array<{path: string; brand_id: string}>;
  feature_ids_all: string[];
  resolved_features: Array<{path: string; feature_id: string}>;
  resolved_specs: ResolvedSpec[];
  unapplied_filters: FilterDiagnostic[];
  conflicts: Array<{attribute_id: string; paths: string[]}>;
  all_requested_filters_applied: boolean;
  price: {min: string | null; max: string | null; currency: "THB"};
}
export interface TypedFilterResolution {
  resolver_version: "typed-filters-v1";
  status: "resolved" | "needs_clarification" | "constraint_conflict";
  all_requested_filters_applied: boolean;
  branches: TypedFilterBranch[];
}

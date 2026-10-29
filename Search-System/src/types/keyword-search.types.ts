import type {FactValue} from "./product-facts.types.js";
import type {TypedFilterResolution} from "./typed-filter.types.js";
/** Internal diagnostics; Commit 16 maps facts/canonical units to the strict public contract. */
export interface KeywordDifference {
 path: string;
 requested_value: FactValue | FactValue[];
 actual_value: FactValue | FactValue[] | null;
 reason: string;
 attribute_id?: string;
 canonical_unit?: string | null;
 requested_numeric_decimal?: string | null;
 actual_numeric_decimal?: string | null;
 input_value?: FactValue;
 input_unit?: string | null;
}
export interface KeywordSearchHit {
 article_id: number;
 category_id: string;
 is_mock: boolean;
 mock_notice: string;
 match_type: "matches_applied_filters" | "alternative";
 keyword_score: number;
 keyword_rank?:number|null;vector_rank?:number|null;vector_score?:number|null;rrf_score?:number|null;
 difference_count: number;
 unknown_count: number;
 numeric_distance: number;
 unmet_filters: KeywordDifference[];
 unknown_filters: KeywordDifference[];
}
export interface KeywordSearchEnvelope {
 engine_version: "keyword-v2" | "hybrid-v1";
 status: "ok" | "no_matches" | "needs_clarification" | "constraint_conflict";
 semantic_available: boolean;
 warnings: Array<{code: string; message: string}>;
 issues?: Array<{path: string; reason: string}>;
 filter_resolution?: TypedFilterResolution;
 resolved_categories?: string[];
 meta?: {page: number;limit: number;total_matches: number;total_alternatives: number;
   returned_count: number;has_more: boolean;all_requested_filters_applied: boolean};
 results: KeywordSearchHit[];
}

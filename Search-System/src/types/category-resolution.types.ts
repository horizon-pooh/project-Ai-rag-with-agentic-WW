export interface CategoryCandidate {
  category_id: string;
  parent_id: string | null;
  level: number;
  name_th: string;
  name_en: string | null;
  matched_label: string;
  match_source: "id" | "name" | "alias";
  match_kind: "exact" | "similar";
  score: number;
  total_candidates: number;
}
export interface CategoryCandidateGroup { phrase_index: number; candidates: CategoryCandidate[] }
export interface CategoryLeafScope {
  root_id: string;
  category_id: string;
  name_th: string;
  path_ids: string[];
  path_labels: string[];
}
export interface CategoryResolutionRepository {
  findCandidates(phrases: string[]): Promise<CategoryCandidateGroup[]>;
  expandRoots(rootIds: string[]): Promise<CategoryLeafScope[]>;
}
export interface CategoryGroupResolution {
  phrase: string;
  status: "resolved" | "needs_clarification";
  reason: "exact_label" | "equivalent_scopes" | "ambiguous_exact_label" |
    "candidate_limit_exceeded" | "similar_labels_require_confirmation" | "unknown_category" | "empty_category_scope";
  candidates: CategoryCandidate[];
  selected_root_id: string | null;
  equivalent_root_ids: string[];
  candidate_total: number;
}
export interface CategoryResolution {
  status: "resolved" | "needs_clarification";
  resolver_version: "category-lexical-v1";
  source_path: "/filters/category_query";
  query: string;
  groups: CategoryGroupResolution[];
  category_ids: string[];
  scopes: CategoryLeafScope[];
}

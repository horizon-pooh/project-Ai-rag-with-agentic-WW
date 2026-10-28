import type {CategoryResolution} from "./category-resolution.types.js";
export interface CategorySemanticState {status:"ready"|"model_unavailable"|"coverage_incomplete";total:number;fresh:number}
export interface SemanticCategoryCandidate {category_id:string;parent_id:string|null;level:number;name_th:string;name_en:string|null;score:number;total_candidates:number}
export interface SemanticCategoryEvidence {
 status:CategorySemanticState["status"]|"identity_lock"|"anchor_limit_exceeded";
 candidates:SemanticCategoryCandidate[];anchor_roots:string[];scope_size:number;
}
export interface CategorySemanticPrepared {
 lexical:CategoryResolution;state:CategorySemanticState;
 vectors:Array<{index:number;phrase:string;vector:number[]}>;
 warning:{code:string;message:string}|null;
}

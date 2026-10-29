export interface ProductSemanticState {
 status:"ready"|"model_unavailable"|"coverage_incomplete"|"empty_scope"|"scope_too_large"|"identity_lock"|"filters_unresolved";
 total:number;fresh:number;
}
export interface ProductHybridPrepared {
 scope:string[];vector:number[]|null;warning:{code:string;message:string}|null;
}

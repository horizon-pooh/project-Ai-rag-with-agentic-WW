import type {QueryEmbeddingCache} from "../embeddings/query-embedding-cache.js";
import type {PoolClient} from "pg";
import {createLlamaEmbeddingProvider} from "../embeddings/llama-embedding.provider.js";
import {EmbeddingError,type EmbeddingResult} from "../embeddings/embedding.types.js";
import {embeddingConfigurationHash} from "../config/embedding.config.js";
import type {CategorySemanticConfig} from "../config/category-semantic.config.js";
import type {CategoryResolution,CategoryCandidate,CategoryGroupResolution} from "../types/category-resolution.types.js";
import type {CategorySemanticState,CategorySemanticPrepared,SemanticCategoryEvidence} from "../types/category-semantic.types.js";
import {createCategoryResolutionRepository} from "../repositories/category-resolution.repository.js";
import {CatalogDataError} from "../types/catalog-read.types.js";
export interface QueryEmbeddingProvider {embed(input:{texts:readonly string[];purpose:"query";signal?:AbortSignal}):Promise<EmbeddingResult>}
type Database=Pick<PoolClient,"query">;
export function createCategorySemanticEngine(rawConfig:CategorySemanticConfig,provider?:QueryEmbeddingProvider,options:{queryCache?:QueryEmbeddingCache}={}) {
 const config=structuredClone(rawConfig);
 if(!/^EM\d{4}$/.test(config.modelId) || !/^[0-9a-f]{64}$/.test(config.artifactSha256) || embeddingConfigurationHash(config.profile)!==config.configurationHash ||
  (config.policy.autoSelect && (config.policy.minScore===null || !Number.isFinite(config.policy.minScore) || config.policy.minScore<0 || config.policy.minScore>1 ||
  config.policy.minGap===null || !Number.isFinite(config.policy.minGap) || config.policy.minGap<=0 || config.policy.minGap>1 || !config.policy.calibrationId)))throw new Error("Invalid semantic configuration");
 const rawProvider=provider??createLlamaEmbeddingProvider({...config,bounds:{...config.bounds,maxAttempts:1,attemptTimeoutMs:4000,totalTimeoutMs:5000}});
 const embedder=options.queryCache?options.queryCache.wrap(rawProvider,{...config,maxInputBytes:config.bounds.maxInputBytes}):rawProvider;
 const scalarParams=[config.modelId,config.configurationHash,config.artifactSha256];
 const needed=(group:CategoryGroupResolution)=>group.status!=="resolved" &&
  (["unknown_category","similar_labels_require_confirmation"].includes(group.reason) ||
   (group.reason==="candidate_limit_exceeded" && group.candidates.length>0 && group.candidates.every(c=>c.match_kind==="similar"))) && !/^[a-z]{2}\d{4}$/i.test(group.phrase);
 return {
  async readiness(db:Database):Promise<CategorySemanticState> {
   const r=(await db.query<{state:CategorySemanticState}>("SELECT catalog_search.category_semantic_state_v1($1,$2,$3) AS state",scalarParams)).rows[0]?.state;
   if(!r || !["ready","model_unavailable","coverage_incomplete"].includes(r.status) || !Number.isInteger(r.total) || !Number.isInteger(r.fresh) || r.total<r.fresh || r.fresh<0)throw new CatalogDataError();
   return r;
  },
  hasWork(lexical:CategoryResolution):boolean {
   return lexical.status==="needs_clarification" && !lexical.input_category_ids && lexical.groups.some(needed);
  },
  /** Called only OUTSIDE the repository transaction/checked-out connection. */
  async prepare(lexical:CategoryResolution,state:CategorySemanticState):Promise<CategorySemanticPrepared> {
   const out:CategorySemanticPrepared={lexical:structuredClone(lexical),state,vectors:[],warning:null,cache_warnings:[]};
   if(state.status!=="ready")return {...out,warning:{code:"category_semantic_"+state.status,message:"Category semantic evidence is unavailable; lexical resolution retained."}};
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
   try {
    for(const [index,group] of lexical.groups.entries())if(needed(group)){
     const r=await embedder.embed({texts:[group.phrase],purpose:"query",signal:controller.signal});
     if(r.configurationHash!==config.configurationHash || r.model!==config.profile.model || r.dimensions!==config.profile.dimensions || r.purpose!=="query" ||
      r.vectors.length!==1 || r.vectors[0]!.length!==config.profile.dimensions || !r.vectors[0]!.every(v=>Number.isFinite(v) && Number.isFinite(Math.fround(v))) ||
      Math.abs(Math.hypot(...r.vectors[0]!)-1)>0.01)throw new EmbeddingError("invalid_response");
     out.vectors.push({index,phrase:group.phrase,vector:r.vectors[0]!});
     if(r.cache_status==="hit" || r.cache_status==="shared")out.cache_warnings!.push({code:r.cache_status==="hit"?"category_query_embedding_cached":"category_query_embedding_shared",message:"ใช้เวกเตอร์คำหมวดที่ตรง profile จาก cache หรืองานร่วม"});
    }
    return out;
   }catch(error){
    if(!(error instanceof EmbeddingError))throw error;
    // Never apply a partial set of an explicit multi-phrase request.
    return {...out,vectors:[],cache_warnings:[],warning:{code:"category_semantic_"+error.code,message:"Category embedding unavailable; lexical resolution retained."}};
   }finally{clearTimeout(timer);}
  },
  async resolve(db:Database,current:CategoryResolution,prepared:CategorySemanticPrepared):Promise<CategoryResolution> {
   // Rerun lexical selectors in the final search snapshot; exact evidence always takes precedence.
   if(!this.hasWork(current))return current;
   if(current.query!==prepared.lexical.query || current.groups.length!==prepared.lexical.groups.length)throw new CatalogDataError();
   if(prepared.warning)return {...current,semantic_warnings:[prepared.warning],selector_issues:[...(current.selector_issues??[]),{path:current.source_path,reason:prepared.warning.code,candidates:[]}]};
   const groups=structuredClone(current.groups);let used=false,selected=false;
   for(const v of prepared.vectors){
    const group=groups[v.index];if(!group || group.phrase!==v.phrase)throw new CatalogDataError();if(!needed(group))continue;
    const e=(await db.query<{evidence:SemanticCategoryEvidence}>("SELECT catalog_search.category_semantic_candidates_v1($1,$2::extensions.vector,$3,$4,$5,8) AS evidence",
     [v.phrase,JSON.stringify(v.vector),...scalarParams])).rows[0]?.evidence;
    if(!e || !Array.isArray(e.candidates) || e.candidates.length>8 || !Array.isArray(e.anchor_roots))throw new CatalogDataError();
    if(e.status!=="ready")return {...current,semantic_warnings:[{code:"category_semantic_"+e.status,message:"Category evidence changed before search; lexical resolution retained."}],selector_issues:[...(current.selector_issues??[]),{path:current.source_path,reason:"category_semantic_"+e.status,candidates:[]}]};
    const candidates:CategoryCandidate[]=e.candidates.map(c=>{
     if(!/^[A-Z]{2}\d{4}$/.test(c.category_id) || c.level!==3 || !Number.isFinite(c.score) || c.score < -1.000001 || c.score>1.000001)throw new CatalogDataError();
     return {...c,matched_label:c.name_th,match_source:"embedding",match_kind:"semantic"};
    });
    const first=candidates[0],second=candidates[1];used=true;
    // No guessed production thresholds. Automatic selection requires an explicit calibration record.
    const gap=first && second?first.score-second.score:null;
    const uniqueAnchor=e.anchor_roots.length===1 && e.scope_size===1;
    const choose=config.policy.autoSelect && first && e.anchor_roots.length<=1 && first.score>=config.policy.minScore! &&
     ((gap!==null && gap>=config.policy.minGap!) || uniqueAnchor);
    if(choose){selected=true;groups[v.index]={...group,status:"resolved",reason:"semantic_calibrated",candidates,
     candidate_total:first.total_candidates,selected_root_id:first.category_id,equivalent_root_ids:[first.category_id]};}
    else if(candidates.length)groups[v.index]={...group,status:"needs_clarification",reason:e.anchor_roots.length>1?"semantic_anchor_ambiguity":"semantic_candidates_require_confirmation",
     candidates,candidate_total:first!.total_candidates,selected_root_id:null,equivalent_root_ids:[]};
   }
   if(!used)return current;
   const resolved=groups.every(g=>g.status==="resolved");
   const scopes=resolved?await createCategoryResolutionRepository(db).expandRoots([...new Set(groups.map(g=>g.selected_root_id!))]):[];
   return {...current,status:resolved?"resolved":"needs_clarification",resolver_version:"category-semantic-v1",groups,
    category_ids:[...new Set(scopes.map(s=>s.category_id))].sort(),scopes,
    semantic_warnings:[{code:selected?"category_semantic_scope_selected":"category_semantic_candidates",message:selected?
     "เลือกหมวดจากเวกเตอร์ตามเกณฑ์ที่ตั้งไว้":"Category suggestions use semantic similarity and require confirmation; scores are not probabilities."},...new Map((prepared.cache_warnings??[]).map(w=>[w.code,w])).values()]};
  },
 };
}
export type CategorySemanticEngine=ReturnType<typeof createCategorySemanticEngine>;

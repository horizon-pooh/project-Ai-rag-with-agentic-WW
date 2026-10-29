import type {PoolClient} from "pg";
import type {ProductHybridConfig} from "../config/product-hybrid.config.js";
import {embeddingConfigurationHash} from "../config/embedding.config.js";
import {createLlamaEmbeddingProvider} from "../embeddings/llama-embedding.provider.js";
import {EmbeddingError} from "../embeddings/embedding.types.js";
import type {QueryEmbeddingProvider} from "./category-semantic.service.js";
import type {ProductHybridPrepared,ProductSemanticState} from "../types/product-hybrid.types.js";
import type {NormalizedToolSearchRequest} from "../types/tool-search.types.js";
import type {TypedFilterResolution} from "../types/typed-filter.types.js";
import type {KeywordSearchEnvelope} from "../types/keyword-search.types.js";
import {CatalogDataError} from "../types/catalog-read.types.js";
type Database=Pick<PoolClient,"query">;
export function createProductHybridEngine(raw:ProductHybridConfig,provider?:QueryEmbeddingProvider) {
 const config=structuredClone(raw);
 if(!/^EM\d{4}$/.test(config.modelId) || !/^[0-9a-f]{64}$/.test(config.artifactSha256) || embeddingConfigurationHash(config.profile)!==config.configurationHash)throw new Error("Invalid hybrid configuration");
 const embedder=provider??createLlamaEmbeddingProvider({...config,bounds:{...config.bounds,maxAttempts:1,attemptTimeoutMs:4000,totalTimeoutMs:5000}});
 return {
  requested(request:NormalizedToolSearchRequest):boolean {
   const q=request.query.trim();return request.sort==="relevance" && !request.exact && !/^1\d{5}$/.test(q) && !/^[A-Z]{2}\d{4}-\d{3,}$/.test(q) && Boolean((request.semantic_query??q).trim());
  },
  async readiness(db:Database,scope:string[],request:NormalizedToolSearchRequest):Promise<ProductSemanticState> {
   const typed=(await db.query<{r:TypedFilterResolution}>("SELECT catalog_search.resolve_typed_filters_v2($1::text[],$2::jsonb) r",[scope,JSON.stringify(request.filters)])).rows[0]?.r;
   if(!typed || !Array.isArray(typed.branches))throw new CatalogDataError();
   if(typed.status!=="resolved")return {status:"filters_unresolved",total:0,fresh:0};
   const valid=typed.branches.filter(b=>b.status==="ready" || b.status==="partial").map(b=>b.category_id);
   if(!valid.length)return {status:"filters_unresolved",total:0,fresh:0};
   const identity=(await db.query<{locked:boolean}>("SELECT EXISTS(SELECT 1 FROM public.products p WHERE p.status='published' AND p.model=$1) AS locked",[request.query.trim()])).rows[0]?.locked;
   if(identity)return {status:"identity_lock",total:0,fresh:0};
   const r=(await db.query<{state:ProductSemanticState}>("SELECT catalog_search.product_semantic_state_v1($1::text[],$2,$3,$4) state",
    [valid,config.modelId,config.configurationHash,config.artifactSha256])).rows[0]?.state;
   if(!r || !["ready","model_unavailable","coverage_incomplete","empty_scope","scope_too_large"].includes(r.status) ||
    !Number.isInteger(r.total) || !Number.isInteger(r.fresh) || r.total<r.fresh || r.fresh<0)throw new CatalogDataError();
   return r;
  },
  // เรียกหลังคืน connection ฐานแล้ว ไม่ค้าง transaction ระหว่างรอโมเดล
  async prepare(scope:string[],request:NormalizedToolSearchRequest,state:ProductSemanticState):Promise<ProductHybridPrepared> {
   const out:ProductHybridPrepared={scope:[...scope].sort(),vector:null,warning:null};
   if(state.status!=="ready")return {...out,warning:{code:"product_hybrid_"+state.status,message:"เวกเตอร์ในขอบเขตยังไม่พร้อม ใช้ keyword"}};
   try {
    const r=await embedder.embed({texts:[(request.semantic_query??request.query).trim()],purpose:"query",signal:AbortSignal.timeout(5000)});
    if(r.configurationHash!==config.configurationHash || r.model!==config.profile.model || r.dimensions!==config.profile.dimensions || r.purpose!=="query" ||
     r.vectors.length!==1 || r.vectors[0]!.length!==config.profile.dimensions || !r.vectors[0]!.every(n=>Number.isFinite(n) && Number.isFinite(Math.fround(n))) ||
     Math.abs(Math.hypot(...r.vectors[0]!)-1)>0.01)throw new EmbeddingError("invalid_response");
    return {...out,vector:r.vectors[0]!};
   }catch(e){if(!(e instanceof EmbeddingError))throw e;return {...out,warning:{code:"product_hybrid_"+e.code,message:"โมเดลสร้างเวกเตอร์ไม่พร้อม ใช้ keyword"}};}
  },
  async search(db:Database,scope:string[],request:NormalizedToolSearchRequest,prepared:ProductHybridPrepared):Promise<KeywordSearchEnvelope> {
   const same=[...scope].sort().join(",")===prepared.scope.join(",");
   if(!prepared.vector || !same) {
    const r=(await db.query<{result:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb) result",[scope,JSON.stringify(request)])).rows[0]?.result;
    if(!r)throw new CatalogDataError();
    const warning=!same?{code:"product_hybrid_scope_changed",message:"ขอบเขตหมวดเปลี่ยนก่อนค้น ใช้ keyword"}:prepared.warning;
    if(warning)r.warnings=[...r.warnings,warning];return r;
   }
   const r=(await db.query<{result:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_hybrid_v2($1::text[],$2::jsonb,$3::extensions.vector,$4,$5,$6) result",
    [scope,JSON.stringify(request),JSON.stringify(prepared.vector),config.modelId,config.configurationHash,config.artifactSha256])).rows[0]?.result;
   if(!r || (r.semantic_available && r.engine_version!=="hybrid-v2"))throw new CatalogDataError();return r;
  },
 };
}
export type ProductHybridEngine=ReturnType<typeof createProductHybridEngine>;

import {randomUUID} from "node:crypto";
import type {PoolClient} from "pg";
import {embeddingConfigurationHash} from "../config/embedding.config.js";
import type {EmbeddingProfile} from "../embeddings/embedding.types.js";
import {verifiedModelSettings,type DocumentKind,type SourceDocument,type ClaimedDocument} from "../embeddings/backfill.types.js";
type Database=Pick<PoolClient,"query">;
// Identifiers come only from this closed map. All caller values remain SQL parameters.
const schema={
 category:{documents:"public.category_search_documents",vectors:"public.category_embeddings",key:"category_id",hash:"source_hash",storedHash:"document_hash",scope:"true"},
 product:{documents:"public.product_search_documents",vectors:"public.product_embeddings",key:"product_id",hash:"content_hash",storedHash:"content_hash",
  scope:"EXISTS(SELECT 1 FROM public.products p WHERE p.id=d.product_id AND p.status='published')"}
} as const;
const idParam=(kind:DocumentKind)=>kind==="product"?"$1::integer":"$1::text";
export class EmbeddingBackfillRepository {
 private selected:{id:string;signature:string}|undefined;
 constructor(readonly db:Database){}
 async assertModel(modelId:string,profile:EmbeddingProfile,sha:string):Promise<void> {
  if(!/^EM\d{4}$/.test(modelId))throw new Error("Invalid model ID");
  this.selected=undefined;
  const r=await this.db.query(`SELECT 1 FROM public.embedding_models WHERE id=$1 AND is_active AND provider=$2 AND model_name=$3
   AND dimensions=$4 AND distance_metric=$5 AND settings=$6::jsonb`,[modelId,profile.provider,profile.model,profile.dimensions,profile.distanceMetric,
    JSON.stringify(verifiedModelSettings(profile,embeddingConfigurationHash(profile),sha))]);
  if(r.rows.length!==1)throw new Error("Selected active model does not match verified profile/artifact");
  this.selected={id:modelId,signature:JSON.stringify([profile.provider,profile.model,profile.dimensions,profile.distanceMetric,verifiedModelSettings(profile,embeddingConfigurationHash(profile),sha)])};
 }
 async page(kind:DocumentKind,after:string|null,limit:number,modelId:string|null=null,maxAttempts=3):Promise<SourceDocument[]> {
  if(!Number.isInteger(limit) || limit<1 || limit>200)throw new Error("Invalid source page bound");
  const s=schema[kind];return (await this.db.query<SourceDocument>(`SELECT d.${s.key}::text AS id,d.embedding_text AS text,d.${s.hash} AS hash,d.template_version AS template
   FROM ${s.documents} d LEFT JOIN ${s.vectors} e ON e.${s.key}=d.${s.key} AND e.model_id=$3
   WHERE ${s.scope} AND ($3::text IS NULL OR e.model_id IS NULL OR ((e.lease_until IS NULL OR e.lease_until<=clock_timestamp()) AND
    (e.${s.storedHash} IS DISTINCT FROM d.${s.hash} OR e.template_version IS DISTINCT FROM d.template_version OR
    ((e.status IN ('pending','stale') OR (e.status='failed' AND e.next_retry_at IS NOT NULL)) AND e.attempt_count<$4 AND (e.next_retry_at IS NULL OR e.next_retry_at<=clock_timestamp())))))
   AND ($1::${kind==="product"?"integer":"text"} IS NULL OR d.${s.key}>$1::${kind==="product"?"integer":"text"})
   ORDER BY d.${s.key} LIMIT $2`,[after,limit,modelId,maxAttempts])).rows;
 }
 /** One atomic upsert acquires a bounded lease; no transaction survives the HTTP call. */
 async claim(kind:DocumentKind,doc:SourceDocument,modelId:string,maxAttempts:number,leaseSeconds:number):Promise<ClaimedDocument|null> {
  if(this.selected?.id!==modelId)throw new Error("Verify model before claiming");
  const s=schema[kind],lease=randomUUID();
  const r=await this.db.query<{attempt:number}>(`INSERT INTO ${s.vectors} AS e
   (${s.key},model_id,${s.storedHash},template_version,status,attempt_count,lease_token,lease_until)
   SELECT d.${s.key},$2,d.${s.hash},d.template_version,'pending',1,$5::uuid,clock_timestamp()+$6::integer*interval '1 second'
   FROM ${s.documents} d WHERE d.${s.key}=${idParam(kind)} AND ${s.scope} AND d.${s.hash}=$3 AND d.template_version=$4
   AND EXISTS(SELECT 1 FROM public.embedding_models m WHERE m.id=$2 AND m.is_active AND jsonb_build_array(m.provider,m.model_name,m.dimensions,m.distance_metric,m.settings)=$8::jsonb)
   ON CONFLICT(${s.key},model_id) DO UPDATE SET ${s.storedHash}=excluded.${s.storedHash},template_version=excluded.template_version,
    status='pending',embedding=null,embedded_at=null,error_message=null,next_retry_at=null,
    attempt_count=CASE WHEN e.${s.storedHash} IS DISTINCT FROM excluded.${s.storedHash} OR e.template_version IS DISTINCT FROM excluded.template_version
     THEN 1 ELSE e.attempt_count+1 END,lease_token=excluded.lease_token,lease_until=excluded.lease_until
   WHERE (e.lease_until IS NULL OR e.lease_until<=clock_timestamp()) AND
    (e.${s.storedHash} IS DISTINCT FROM excluded.${s.storedHash} OR e.template_version IS DISTINCT FROM excluded.template_version OR
     ((e.status IN ('pending','stale') OR (e.status='failed' AND e.next_retry_at IS NOT NULL))
      AND e.attempt_count<$7 AND (e.next_retry_at IS NULL OR e.next_retry_at<=clock_timestamp())))
   RETURNING attempt_count AS attempt`,[doc.id,modelId,doc.hash,doc.template,lease,leaseSeconds,maxAttempts,this.selected.signature]);
  return r.rows[0]?{...doc,lease,attempt:r.rows[0].attempt}:null;
 }
 /** Compare-and-set fences old workers, source edits, expired leases, and deactivated models. */
 async finish(kind:DocumentKind,doc:ClaimedDocument,modelId:string,result:{vector:number[]}|{error:string;retrySeconds:number|null}):Promise<boolean> {
  if(this.selected?.id!==modelId)throw new Error("Verify model before completion");
  const s=schema[kind];const vector="vector" in result?JSON.stringify(result.vector):null;
  if(vector===null && !("error" in result && /^[a-z_]{1,60}$/.test(result.error)))throw new Error("Unsafe error diagnostic");
  const r=await this.db.query(`UPDATE ${s.vectors} e SET embedding=$7::extensions.vector,status=$8,embedded_at=CASE WHEN $7::text IS NOT NULL THEN clock_timestamp() ELSE NULL END,
    error_message=$9,next_retry_at=CASE WHEN $10::integer IS NULL THEN NULL ELSE clock_timestamp()+$10::integer*interval '1 second' END,
    lease_token=null,lease_until=null
   WHERE e.${s.key}=${idParam(kind)} AND e.model_id=$2 AND e.${s.storedHash}=$3 AND e.template_version=$4
    AND e.status='pending' AND e.lease_token=$5::uuid AND e.attempt_count=$6 AND e.lease_until>clock_timestamp()
    AND EXISTS(SELECT 1 FROM ${s.documents} d WHERE d.${s.key}=e.${s.key} AND d.${s.hash}=e.${s.storedHash}
     AND d.template_version=e.template_version AND ${s.scope})
    AND EXISTS(SELECT 1 FROM public.embedding_models m WHERE m.id=e.model_id AND m.is_active AND jsonb_build_array(m.provider,m.model_name,m.dimensions,m.distance_metric,m.settings)=$11::jsonb)
   RETURNING e.${s.key}`,[doc.id,modelId,doc.hash,doc.template,doc.lease,doc.attempt,vector,vector===null?"failed":"ready",
    "error" in result?result.error:null,"error" in result?result.retrySeconds:null,this.selected.signature]);
  return r.rows.length===1;
 }
 async coverage(kind:DocumentKind,modelId:string) {
  const s=schema[kind];return (await this.db.query<{total:number;fresh:number;pending:number;failed:number;stale:number}>(`SELECT count(*)::integer AS total,
   count(*) FILTER(WHERE e.status='ready' AND e.${s.storedHash}=d.${s.hash} AND e.template_version=d.template_version AND m.is_active
    AND extensions.vector_dims(e.embedding)=m.dimensions)::integer AS fresh,
   count(*) FILTER(WHERE e.status='pending')::integer AS pending,count(*) FILTER(WHERE e.status='failed')::integer AS failed,
   count(*) FILTER(WHERE e.status='stale' OR (e.status='ready' AND (e.${s.storedHash} IS DISTINCT FROM d.${s.hash} OR e.template_version IS DISTINCT FROM d.template_version OR NOT m.is_active)))::integer AS stale
   FROM ${s.documents} d LEFT JOIN ${s.vectors} e ON e.${s.key}=d.${s.key} AND e.model_id=$1
   LEFT JOIN public.embedding_models m ON m.id=e.model_id WHERE ${s.scope}`,[modelId])).rows[0]!;
 }
}
/** Owner-only registration: exact rerun permitted; never overwrite or deactivate another configuration. Caller owns transaction. */
export async function registerEmbeddingModel(db:Database,id:string,profile:EmbeddingProfile,sha:string):Promise<void> {
 if(!/^EM\d{4}$/.test(id))throw new Error("Invalid model ID");
 const settings=verifiedModelSettings(profile,embeddingConfigurationHash(profile),sha);
 await db.query("SELECT pg_advisory_xact_lock(78120620)");
 const existing=(await db.query<{matches:boolean;is_active:boolean}>(`SELECT provider=$2 AND model_name=$3 AND dimensions=$4 AND distance_metric=$5 AND settings=$6::jsonb AS matches,is_active
  FROM public.embedding_models WHERE id=$1`,[id,profile.provider,profile.model,profile.dimensions,profile.distanceMetric,JSON.stringify(settings)])).rows[0];
 if(existing){if(!existing.matches || !existing.is_active)throw new Error("Model ID already registered with different/inactive configuration");return;}
 await db.query(`INSERT INTO public.embedding_models(id,provider,model_name,dimensions,distance_metric,settings,is_active) VALUES($1,$2,$3,$4,$5,$6::jsonb,true)`,
  [id,profile.provider,profile.model,profile.dimensions,profile.distanceMetric,JSON.stringify(settings)]);
}

import type {PoolClient} from "pg";
import type {EmbeddingProfile} from "../embeddings/embedding.types.js";
import {embeddingConfigurationHash} from "../config/embedding.config.js";
export async function inspectEmbeddingStorage(client:Pick<PoolClient,"query">,profile?:EmbeddingProfile) {
 const configurationHash=profile?embeddingConfigurationHash(profile):undefined;
 const extension=(await client.query<{version:string;schema:string}>("SELECT e.extversion AS version,n.nspname AS schema FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'")).rows;
 const columns=(await client.query<{table_name:string;column_name:string;data_type:string}>(`
  SELECT c.relname AS table_name,a.attname AS column_name,format_type(a.atttypid,a.atttypmod) AS data_type
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
  WHERE n.nspname='public' AND c.relname IN ('embedding_models','product_embeddings','category_embeddings','product_search_documents','category_search_documents')
  AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum`)).rows;
 const relations=(await client.query<{table_name:string;rls:boolean}>("SELECT c.relname AS table_name,c.relrowsecurity AS rls FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname IN ('embedding_models','product_embeddings','category_embeddings','category_search_documents','product_search_documents') ORDER BY c.relname")).rows;
 const rlsReady=relations.length===5 && relations.every(r=>r.rls);
 const triggers=(await client.query<{table_name:string;name:string;enabled:string;security_definer:boolean;safe_search_path:boolean}>(`
  SELECT c.relname AS table_name,t.tgname AS name,t.tgenabled AS enabled,p.prosecdef AS security_definer,
   coalesce('search_path=""'=ANY(p.proconfig),false) AS safe_search_path
  FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
  WHERE NOT t.tgisinternal AND n.nspname='public' AND t.tgname IN ('embedding_valid','embedding_stale','search_v2_category_embedding_guard','search_v2_category_doc_stale','search_v2_model_config_guard') ORDER BY t.tgname`)).rows;
 const models=(await client.query<{id:string;provider:string;model_name:string;dimensions:number;distance_metric:string;is_active:boolean;configuration_hash:string|null;has_artifact_sha256:boolean;matches_profile:boolean}>(`
  SELECT id,provider,model_name,dimensions,distance_metric,is_active,settings->>'configuration_hash' AS configuration_hash,
   coalesce(settings->>'artifact_sha256' ~ '^[0-9a-f]{64}$',false) AS has_artifact_sha256,
   coalesce(settings->>'configuration_hash'=$1,false) AS matches_profile FROM public.embedding_models ORDER BY id`,[configurationHash??null])).rows;
 const counts=(await client.query<{products:number;product_documents:number;category_documents:number;product_embeddings:number;category_embeddings:number}>(`
  SELECT (SELECT count(*)::integer FROM public.products) AS products,(SELECT count(*)::integer FROM public.product_search_documents) AS product_documents,
   (SELECT count(*)::integer FROM public.category_search_documents) AS category_documents,(SELECT count(*)::integer FROM public.product_embeddings) AS product_embeddings,
   (SELECT count(*)::integer FROM public.category_embeddings) AS category_embeddings`)).rows[0]!;
 const coverage=(await client.query<{kind:string;model_id:string;total:number;ready:number;fresh:number}>(`
  WITH rows AS (
   SELECT 'product' AS kind,e.model_id,e.status,m.is_active,e.embedding,m.dimensions,m.distance_metric,m.provider,m.settings,
    e.content_hash=d.content_hash AND e.template_version=d.template_version AND p.status='published' AS source_current
   FROM public.product_embeddings e JOIN public.embedding_models m ON m.id=e.model_id JOIN public.products p ON p.id=e.product_id
   LEFT JOIN public.product_search_documents d ON d.product_id=e.product_id
   UNION ALL
   SELECT 'category',e.model_id,e.status,m.is_active,e.embedding,m.dimensions,m.distance_metric,m.provider,m.settings,
    e.document_hash=d.source_hash AND e.template_version=d.template_version
   FROM public.category_embeddings e JOIN public.embedding_models m ON m.id=e.model_id LEFT JOIN public.category_search_documents d ON d.category_id=e.category_id)
  SELECT kind,model_id,count(*)::integer AS total,count(*) FILTER(WHERE status='ready')::integer AS ready,
   count(*) FILTER(WHERE status='ready' AND is_active AND source_current AND embedding IS NOT NULL AND extensions.vector_dims(embedding)=dimensions
    AND (distance_metric<>'cosine' OR extensions.vector_norm(embedding)>0)
    AND (provider<>'llama_cpp' OR (settings->>'configuration_hash' ~ '^[0-9a-f]{64}$' AND settings->>'artifact_sha256' ~ '^[0-9a-f]{64}$'
      AND abs(extensions.vector_norm(embedding)-1)<=0.01)))::integer AS fresh
  FROM rows GROUP BY kind,model_id ORDER BY kind,model_id`)).rows;
 const required=["created_at","updated_at","attempt_count","next_retry_at","lease_token","lease_until"];
 const leases=required.every(name=>columns.some(c=>c.table_name==="product_embeddings" && c.column_name===name));
 const dimensionUnlocked=["product_embeddings","category_embeddings"].every(t=>columns.some(c=>c.table_name===t && c.column_name==="embedding" && ["extensions.vector","vector"].includes(c.data_type)));
 const guarded=["embedding_valid","embedding_stale","search_v2_category_embedding_guard","search_v2_category_doc_stale"].every(name=>triggers.some(t=>t.name===name && t.enabled==="O" && t.security_definer && t.safe_search_path));
 const immutable=triggers.some(t=>t.name==="search_v2_model_config_guard" && t.enabled==="O" && t.safe_search_path);
 return {extension,columns,relations,rls_ready:rlsReady,triggers,models,counts,coverage,leases,dimension_unlocked:dimensionUnlocked,guards_ready:guarded,model_guard_ready:immutable,
  matching_active_models:models.filter(m=>profile && m.matches_profile && m.is_active && m.has_artifact_sha256 && m.provider===profile.provider && m.model_name===profile.model && m.dimensions===profile.dimensions && m.distance_metric===profile.distanceMetric).map(m=>m.id)};
}

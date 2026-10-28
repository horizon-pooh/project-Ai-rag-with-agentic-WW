-- Up Migration
-- Bounded read-only capability: readers receive category evidence, never vector payloads/model settings.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector' AND n.nspname='extensions') THEN
  RAISE EXCEPTION 'Expected existing vector extension in extensions';END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.product_embeddings'::regclass AND attname='lease_token' AND NOT attisdropped) THEN
  RAISE EXCEPTION 'Apply audited storage migration0007 first';END IF;
 IF EXISTS(SELECT 1 FROM pg_class WHERE oid IN ('public.embedding_models'::regclass,'public.category_embeddings'::regclass,'public.category_search_documents'::regclass)
  AND (NOT relrowsecurity OR relowner<>(SELECT oid FROM pg_roles WHERE rolname=current_user))) THEN
  RAISE EXCEPTION 'Expected owner-owned embedding tables with RLS';END IF;
END $$;
CREATE FUNCTION catalog_search.category_semantic_state_v1(p_model text,p_config_hash text,p_artifact_hash text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path='' AS $$
DECLARE m public.embedding_models; total_count integer;fresh_count integer;
BEGIN
 IF p_model IS NULL OR p_model !~ '^EM[0-9]{4}$' OR p_config_hash IS NULL OR p_config_hash !~ '^[0-9a-f]{64}$'
  OR p_artifact_hash IS NULL OR p_artifact_hash !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid semantic category configuration';END IF;
 SELECT * INTO m FROM public.embedding_models WHERE id=p_model AND is_active AND provider='llama_cpp' AND distance_metric='cosine'
  AND settings->>'configuration_hash'=p_config_hash AND settings->>'artifact_sha256'=p_artifact_hash;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','model_unavailable','total',0,'fresh',0);END IF;
 SELECT count(*)::integer,count(e.category_id) FILTER(WHERE e.status='ready' AND e.document_hash=d.source_hash AND e.template_version=d.template_version
  AND extensions.vector_dims(e.embedding)=m.dimensions AND abs(extensions.vector_norm(e.embedding)-1)<=0.01)::integer
 INTO total_count,fresh_count FROM public.categories c LEFT JOIN public.category_search_documents d ON d.category_id=c.id
 LEFT JOIN public.category_embeddings e ON e.category_id=c.id AND e.model_id=m.id;
 RETURN jsonb_build_object('status',CASE WHEN total_count>0 AND total_count=fresh_count THEN 'ready' ELSE 'coverage_incomplete' END,
  'total',total_count,'fresh',fresh_count);
END $$;
CREATE FUNCTION catalog_search.category_semantic_candidates_v1(p_query text,p_vector extensions.vector,p_model text,p_config_hash text,p_artifact_hash text,p_limit integer DEFAULT 8)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path='' AS $$
DECLARE state jsonb; m public.embedding_models; q text:=catalog_search.normalize_label(p_query); anchors text[]; payload jsonb; scope_count integer;
BEGIN
 IF q IS NULL OR q='' OR char_length(p_query)>200 OR p_limit IS NULL OR p_limit<2 OR p_limit>10 OR p_vector IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid semantic category request';END IF;
 -- Even trusted internal calls cannot use vectors to escape an explicitly supplied unknown category code.
 IF q ~ '^[a-z]{2}[0-9]{4}$' THEN RETURN jsonb_build_object('status','identity_lock','candidates','[]'::jsonb,'anchor_roots','[]'::jsonb,'scope_size',0);END IF;
 state:=catalog_search.category_semantic_state_v1(p_model,p_config_hash,p_artifact_hash);
 IF state->>'status'<>'ready' THEN RETURN state||jsonb_build_object('candidates','[]'::jsonb,'anchor_roots','[]'::jsonb,'scope_size',0);END IF;
 SELECT * INTO STRICT m FROM public.embedding_models WHERE id=p_model;
 IF extensions.vector_dims(p_vector)<>m.dimensions OR abs(extensions.vector_norm(p_vector)-1)>0.01 THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Query vector profile mismatch';END IF;
 -- Labels actually present in this catalog anchor explicit product/subtype words. Never widen OLED to QLED.
 WITH labels AS (
  SELECT c.id,c.parent_id,c.level,catalog_search.normalize_label(l.label) AS norm FROM public.categories c CROSS JOIN LATERAL
   (SELECT c.name_th AS label UNION ALL SELECT c.name_en UNION ALL SELECT a FROM unnest(c.aliases) a) l
  WHERE l.label IS NOT NULL
 ), matched AS MATERIALIZED (
  SELECT * FROM labels l WHERE char_length(l.norm)>=2 AND
   CASE WHEN l.norm ~ '^[a-z0-9 -]+$' THEN q ~ ('(^|[^a-z0-9])'||l.norm||'([^a-z0-9]|$)') ELSE strpos(q,l.norm)>0 END
 ), longest AS (
  SELECT a.* FROM matched a WHERE NOT EXISTS(SELECT 1 FROM matched b WHERE b.norm<>a.norm AND strpos(b.norm,a.norm)>0)
 ), specific AS (
  SELECT DISTINCT a.id FROM longest a WHERE NOT EXISTS(SELECT 1 FROM longest b LEFT JOIN public.categories p ON p.id=b.parent_id
    WHERE b.parent_id=a.id OR p.parent_id=a.id)
 ) SELECT coalesce(array_agg(id ORDER BY id),'{}'::text[]) INTO anchors FROM specific;
 IF cardinality(anchors)>40 THEN RETURN jsonb_build_object('status','anchor_limit_exceeded','candidates','[]'::jsonb,'anchor_roots','[]'::jsonb,'scope_size',0);END IF;
 WITH RECURSIVE allowed(category_id,level) AS (
  SELECT c.id,c.level FROM public.categories c WHERE c.id=ANY(anchors)
  UNION ALL SELECT c.id,c.level FROM public.categories c JOIN allowed a ON c.parent_id=a.category_id AND c.level=a.level+1 WHERE a.level<3
 ), eligible AS MATERIALIZED (
  SELECT c.id AS category_id,c.parent_id,c.level::integer,c.name_th,c.name_en,
   (1-(e.embedding OPERATOR(extensions.<=>) p_vector))::double precision AS score
  FROM public.categories c JOIN public.category_search_documents d ON d.category_id=c.id
   JOIN public.category_embeddings e ON e.category_id=c.id AND e.model_id=m.id
  WHERE c.level=3 AND e.status='ready' AND e.document_hash=d.source_hash AND e.template_version=d.template_version
   AND extensions.vector_dims(e.embedding)=m.dimensions AND (cardinality(anchors)=0 OR c.id IN(SELECT category_id FROM allowed))
 ), counted AS (SELECT e.*,count(*) OVER()::integer AS total_candidates FROM eligible e)
 SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.score DESC,x.category_id),'[]'::jsonb),coalesce(max(x.total_candidates),0)
 INTO payload,scope_count FROM(SELECT * FROM counted ORDER BY score DESC,category_id LIMIT p_limit)x;
 RETURN state||jsonb_build_object('candidates',payload,'anchor_roots',to_jsonb(anchors),'scope_size',scope_count);
END $$;
REVOKE ALL ON FUNCTION catalog_search.category_semantic_state_v1(text,text,text),catalog_search.category_semantic_candidates_v1(text,extensions.vector,text,text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog_search.category_semantic_state_v1(text,text,text),catalog_search.category_semantic_candidates_v1(text,extensions.vector,text,text,text,integer) TO catalog_search_reader;
-- Down Migration
DROP FUNCTION catalog_search.category_semantic_candidates_v1(text,extensions.vector,text,text,text,integer);
DROP FUNCTION catalog_search.category_semantic_state_v1(text,text,text);

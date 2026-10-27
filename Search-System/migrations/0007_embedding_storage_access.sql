-- Up Migration
-- Existing storage is reused. No models/vectors are registered or generated here.
DO $preflight$
DECLARE t text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='catalog_embedding_worker' AND NOT rolcanlogin AND NOT rolsuper
   AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb) THEN RAISE EXCEPTION 'Bootstrap the dedicated worker group first'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector' AND n.nspname='extensions') THEN
   RAISE EXCEPTION 'Expected existing vector extension in extensions schema'; END IF;
 IF encode(sha256(convert_to(pg_get_functiondef('dataset_admin.validate_category_embedding()'::regprocedure),'UTF8')),'hex')<>'5934546073ce9748981213b66413500dc5605a9ee1349b75a10a08f79d4db21d' THEN RAISE EXCEPTION 'Embedding trigger baseline drift';END IF;
 IF encode(sha256(convert_to(pg_get_functiondef('dataset_admin.category_document_changed()'::regprocedure),'UTF8')),'hex')<>'d5decd8b18dad1c91ea3007c2b6a043ed8ec5da5ffe7cb408a7e59eb1650d026' THEN RAISE EXCEPTION 'Embedding trigger baseline drift';END IF;
 IF encode(sha256(convert_to(pg_get_functiondef('public.validate_embedding()'::regprocedure),'UTF8')),'hex')<>'7c0310a28b44574b25d69bef13543fa1e7bede09d632dc2b4a28992c437e1d07' THEN RAISE EXCEPTION 'Embedding trigger baseline drift';END IF;
 IF encode(sha256(convert_to(pg_get_functiondef('public.invalidate_embedding()'::regprocedure),'UTF8')),'hex')<>'a9122b2911278a9a4879276f364ab5a4ee98f89dbbd489426e9f31b2f944c7d0' THEN RAISE EXCEPTION 'Embedding trigger baseline drift';END IF;
 FOREACH t IN ARRAY ARRAY['embedding_models','product_embeddings','category_embeddings','category_search_documents','product_search_documents'] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=t AND c.relkind='r' AND c.relrowsecurity) THEN
   RAISE EXCEPTION 'Missing baseline table or RLS: %',t;END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid IN ('public.product_embeddings'::regclass,'public.category_embeddings'::regclass)
   AND a.attname='embedding' AND (a.atttypid<>'extensions.vector'::regtype OR a.atttypmod<>-1)) THEN RAISE EXCEPTION 'Expected dimension-unlocked vector storage'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname='embedding_models_configuration_v2_key') THEN
  RAISE EXCEPTION 'Expected the audited model configuration unique index';END IF;
END $preflight$;

ALTER TABLE public.product_embeddings
 ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
 ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
 ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 ADD COLUMN next_retry_at timestamptz,
 ADD COLUMN lease_token uuid,
 ADD COLUMN lease_until timestamptz,
 ADD CONSTRAINT embedding_v3_product_lease CHECK ((lease_token IS NULL AND lease_until IS NULL) OR
   (lease_token IS NOT NULL AND lease_until IS NOT NULL AND status='pending' AND attempt_count>0));
ALTER TABLE public.category_embeddings ADD CONSTRAINT embedding_v3_category_lease CHECK
 ((lease_token IS NULL AND lease_until IS NULL) OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND status='pending' AND attempt_count>0));

-- Narrow definer trigger: row locks on model/source do not grant workers UPDATE on those source tables.
-- Only table-owned triggers invoke this function; it is not a general callable write API.
CREATE OR REPLACE FUNCTION public.validate_embedding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE m public.embedding_models; d public.product_search_documents; n double precision;
BEGIN
 SELECT * INTO STRICT m FROM public.embedding_models WHERE id=new.model_id FOR SHARE;
 IF new.embedding IS NOT NULL AND extensions.vector_dims(new.embedding)<>m.dimensions THEN RAISE EXCEPTION 'embedding dimensions mismatch';END IF;
 IF new.status='ready' THEN
  IF NOT m.is_active THEN RAISE EXCEPTION 'Inactive embedding model';END IF;
  SELECT * INTO STRICT d FROM public.product_search_documents WHERE product_id=new.product_id FOR SHARE;
  IF new.content_hash IS DISTINCT FROM d.content_hash OR new.template_version IS DISTINCT FROM d.template_version THEN RAISE EXCEPTION 'embedding content is stale';END IF;
  n:=extensions.vector_norm(new.embedding);
  IF m.distance_metric='cosine' AND (n IS NULL OR n=0) THEN RAISE EXCEPTION 'zero or missing cosine vector';END IF;
  IF m.provider='llama_cpp' AND (NOT coalesce(m.settings->>'configuration_hash' ~ '^[0-9a-f]{64}$',false)
    OR NOT coalesce(m.settings->>'artifact_sha256' ~ '^[0-9a-f]{64}$',false)
    OR abs(n-1)>0.01) THEN RAISE EXCEPTION 'Unverified local model configuration or unnormalized vector';END IF;
 END IF;
 IF new.status<>'pending' THEN new.lease_token:=null;new.lease_until:=null;END IF;
 new.updated_at:=clock_timestamp();RETURN new;
END $$;
CREATE OR REPLACE FUNCTION dataset_admin.validate_category_embedding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE m public.embedding_models; d public.category_search_documents; n double precision;
BEGIN
 SELECT * INTO STRICT m FROM public.embedding_models WHERE id=new.model_id FOR SHARE;
 IF new.embedding IS NOT NULL AND extensions.vector_dims(new.embedding)<>m.dimensions THEN RAISE EXCEPTION 'vector dimension mismatch';END IF;
 IF new.status='ready' THEN
  IF NOT m.is_active THEN RAISE EXCEPTION 'Inactive embedding model';END IF;
  SELECT * INTO STRICT d FROM public.category_search_documents WHERE category_id=new.category_id FOR SHARE;
  IF new.document_hash IS DISTINCT FROM d.source_hash OR new.template_version IS DISTINCT FROM d.template_version THEN RAISE EXCEPTION 'stale category vector';END IF;
  n:=extensions.vector_norm(new.embedding);
  IF m.distance_metric='cosine' AND (n IS NULL OR n=0) THEN RAISE EXCEPTION 'zero or missing cosine vector';END IF;
  IF m.provider='llama_cpp' AND (NOT coalesce(m.settings->>'configuration_hash' ~ '^[0-9a-f]{64}$',false)
    OR NOT coalesce(m.settings->>'artifact_sha256' ~ '^[0-9a-f]{64}$',false)
    OR abs(n-1)>0.01) THEN RAISE EXCEPTION 'Unverified local model configuration or unnormalized vector';END IF;
 END IF;
 IF new.status<>'pending' THEN new.lease_token:=null;new.lease_until:=null;END IF;
 new.updated_at:=clock_timestamp();RETURN new;
END $$;
CREATE OR REPLACE FUNCTION public.invalidate_embedding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 IF new.content_hash IS DISTINCT FROM old.content_hash OR new.template_version IS DISTINCT FROM old.template_version THEN
  UPDATE public.product_embeddings SET status='stale',lease_token=null,lease_until=null,updated_at=clock_timestamp()
   WHERE product_id=new.product_id;
 END IF;RETURN new;
END $$;
CREATE OR REPLACE FUNCTION dataset_admin.category_document_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN
 IF new.source_hash IS DISTINCT FROM old.source_hash OR new.template_version IS DISTINCT FROM old.template_version THEN
  UPDATE public.category_embeddings SET status='stale',lease_token=null,lease_until=null,updated_at=clock_timestamp()
   WHERE category_id=new.category_id;
 END IF;RETURN new;
END $$;
REVOKE ALL ON FUNCTION public.validate_embedding(),public.invalidate_embedding(),dataset_admin.validate_category_embedding(),dataset_admin.category_document_changed() FROM PUBLIC;

GRANT USAGE ON SCHEMA public,extensions TO catalog_embedding_worker;
GRANT SELECT(id,category_id,status) ON public.products TO catalog_embedding_worker;
GRANT SELECT ON public.embedding_models,public.product_search_documents,public.category_search_documents,public.product_embeddings,public.category_embeddings TO catalog_embedding_worker;
GRANT INSERT ON public.product_embeddings,public.category_embeddings TO catalog_embedding_worker;
GRANT UPDATE(embedding,content_hash,template_version,status,embedded_at,error_message,updated_at,attempt_count,next_retry_at,lease_token,lease_until) ON public.product_embeddings TO catalog_embedding_worker;
GRANT UPDATE(embedding,document_hash,template_version,status,embedded_at,error_message,updated_at,attempt_count,next_retry_at,lease_token,lease_until) ON public.category_embeddings TO catalog_embedding_worker;

CREATE POLICY embedding_v3_worker_product_read ON public.products FOR SELECT TO catalog_embedding_worker USING(status='published');
CREATE POLICY embedding_v3_worker_product_guard ON public.products AS RESTRICTIVE FOR SELECT TO catalog_embedding_worker USING(status='published');
CREATE POLICY embedding_v3_worker_document_read ON public.product_search_documents FOR SELECT TO catalog_embedding_worker USING(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published'));
CREATE POLICY embedding_v3_worker_document_guard ON public.product_search_documents AS RESTRICTIVE FOR SELECT TO catalog_embedding_worker USING(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published'));
CREATE POLICY embedding_v3_worker_models_read ON public.embedding_models FOR SELECT TO catalog_embedding_worker USING(true);
CREATE POLICY embedding_v3_worker_categories_read ON public.category_search_documents FOR SELECT TO catalog_embedding_worker USING(true);
CREATE POLICY embedding_v3_worker_product_select ON public.product_embeddings FOR SELECT TO catalog_embedding_worker USING(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published'));
CREATE POLICY embedding_v3_worker_product_insert ON public.product_embeddings FOR INSERT TO catalog_embedding_worker WITH CHECK(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published'));
CREATE POLICY embedding_v3_worker_product_update ON public.product_embeddings FOR UPDATE TO catalog_embedding_worker USING(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published')) WITH CHECK(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published'));
CREATE POLICY embedding_v3_worker_product_scope ON public.product_embeddings AS RESTRICTIVE FOR ALL TO catalog_embedding_worker USING(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published')) WITH CHECK(EXISTS(SELECT 1 FROM public.products p WHERE p.id=product_id AND p.status='published'));
CREATE POLICY embedding_v3_worker_category_select ON public.category_embeddings FOR SELECT TO catalog_embedding_worker USING(true);
CREATE POLICY embedding_v3_worker_category_insert ON public.category_embeddings FOR INSERT TO catalog_embedding_worker WITH CHECK(true);
CREATE POLICY embedding_v3_worker_category_update ON public.category_embeddings FOR UPDATE TO catalog_embedding_worker USING(true) WITH CHECK(true);

-- Down Migration
-- Keep models, sources and payloads. Do not discard real job-progress metadata on rollback.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.product_embeddings WHERE attempt_count<>0 OR lease_token IS NOT NULL OR next_retry_at IS NOT NULL) THEN
  RAISE EXCEPTION 'Product job progress exists; use a reviewed forward repair instead of dropping lease metadata';END IF;
END $$;
DROP POLICY embedding_v3_worker_product_read ON public.products;
DROP POLICY embedding_v3_worker_product_guard ON public.products;
DROP POLICY embedding_v3_worker_document_read ON public.product_search_documents;
DROP POLICY embedding_v3_worker_document_guard ON public.product_search_documents;
DROP POLICY embedding_v3_worker_models_read ON public.embedding_models;
DROP POLICY embedding_v3_worker_categories_read ON public.category_search_documents;
DROP POLICY embedding_v3_worker_product_select ON public.product_embeddings;
DROP POLICY embedding_v3_worker_product_insert ON public.product_embeddings;
DROP POLICY embedding_v3_worker_product_update ON public.product_embeddings;
DROP POLICY embedding_v3_worker_product_scope ON public.product_embeddings;
DROP POLICY embedding_v3_worker_category_select ON public.category_embeddings;
DROP POLICY embedding_v3_worker_category_insert ON public.category_embeddings;
DROP POLICY embedding_v3_worker_category_update ON public.category_embeddings;
REVOKE SELECT(id,category_id,status) ON public.products FROM catalog_embedding_worker;
REVOKE ALL ON public.embedding_models,public.product_search_documents,public.category_search_documents,public.product_embeddings,public.category_embeddings FROM catalog_embedding_worker;
REVOKE UPDATE(embedding,content_hash,template_version,status,embedded_at,error_message,updated_at,attempt_count,next_retry_at,lease_token,lease_until) ON public.product_embeddings FROM catalog_embedding_worker;
REVOKE UPDATE(embedding,document_hash,template_version,status,embedded_at,error_message,updated_at,attempt_count,next_retry_at,lease_token,lease_until) ON public.category_embeddings FROM catalog_embedding_worker;
REVOKE USAGE ON SCHEMA public,extensions FROM catalog_embedding_worker;
ALTER TABLE public.category_embeddings DROP CONSTRAINT embedding_v3_category_lease;
ALTER TABLE public.product_embeddings DROP COLUMN created_at,DROP COLUMN updated_at,DROP COLUMN attempt_count,DROP COLUMN next_retry_at,DROP COLUMN lease_token,DROP COLUMN lease_until;

CREATE OR REPLACE FUNCTION dataset_admin.validate_category_embedding()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
declare m public.embedding_models; d public.category_search_documents;
begin
 select * into strict m from public.embedding_models where id=new.model_id for share;
 if new.embedding is not null and extensions.vector_dims(new.embedding)<>m.dimensions then raise exception 'vector dimension mismatch'; end if;
 if new.status='ready' then
  select * into strict d from public.category_search_documents where category_id=new.category_id for share;
  if new.document_hash<>d.source_hash or new.template_version<>d.template_version then raise exception 'stale category vector'; end if;
  if m.distance_metric='cosine' and extensions.vector_norm(new.embedding)=0 then raise exception 'zero cosine vector'; end if;
 end if;
 new.updated_at=clock_timestamp(); return new;
end $function$;

CREATE OR REPLACE FUNCTION dataset_admin.category_document_changed()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
 update public.category_embeddings set status='stale',updated_at=clock_timestamp(),lease_token=null,lease_until=null
 where category_id=new.category_id and (document_hash<>new.source_hash or template_version<>new.template_version);
 return new;
end $function$;

CREATE OR REPLACE FUNCTION public.validate_embedding()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
declare dims integer; h text; tv text;
begin
 select dimensions into dims from public.embedding_models where id=new.model_id;
 if new.embedding is not null and extensions.vector_dims(new.embedding)<>dims then raise exception 'embedding dimensions mismatch'; end if;
 if new.status='ready' then
 select content_hash,template_version into h,tv from public.product_search_documents where product_id=new.product_id;
 if h is null or new.content_hash<>h or new.template_version<>tv then raise exception 'embedding content is stale'; end if;
 end if;
 return new;
end $function$;

CREATE OR REPLACE FUNCTION public.invalidate_embedding()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
 if new.content_hash is distinct from old.content_hash or new.template_version is distinct from old.template_version then
 update public.product_embeddings set status='stale' where product_id=new.product_id and status='ready';
 end if;
 return new;
end $function$;

GRANT EXECUTE ON FUNCTION public.validate_embedding(),public.invalidate_embedding(),dataset_admin.validate_category_embedding(),dataset_admin.category_document_changed() TO PUBLIC;

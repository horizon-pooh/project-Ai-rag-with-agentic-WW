-- Apply after metadata/category/typed/keyword-products/catalog-facts fixtures.
DROP TABLE public.product_embeddings,public.category_embeddings,public.embedding_models,public.category_search_documents;
CREATE TABLE public.embedding_models(id text PRIMARY KEY CHECK(id~'^EM[0-9]{4}$'),provider text NOT NULL,model_name text NOT NULL,
 dimensions integer NOT NULL CHECK(dimensions>0),distance_metric text NOT NULL CHECK(distance_metric IN ('cosine','inner_product','l2')),
 settings jsonb NOT NULL DEFAULT '{}',is_active boolean NOT NULL DEFAULT false);
CREATE UNIQUE INDEX embedding_models_configuration_v2_key ON public.embedding_models(provider,model_name,dimensions,distance_metric,settings);
CREATE TABLE public.category_search_documents(category_id text PRIMARY KEY REFERENCES public.categories(id),embedding_text text NOT NULL,
 source_hash text NOT NULL,template_version text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(source_hash=encode(extensions.digest(embedding_text,'sha256'),'hex')));
CREATE TABLE public.product_embeddings(product_id integer REFERENCES public.products(id),model_id text REFERENCES public.embedding_models(id),embedding extensions.vector,
 content_hash text NOT NULL,template_version text NOT NULL,status text NOT NULL CHECK(status IN ('pending','ready','stale','failed')),embedded_at timestamptz,error_message text,
 PRIMARY KEY(product_id,model_id),CHECK(status<>'ready' OR (embedding IS NOT NULL AND embedded_at IS NOT NULL AND error_message IS NULL)));
CREATE TABLE public.category_embeddings(category_id text REFERENCES public.category_search_documents(category_id),model_id text REFERENCES public.embedding_models(id),embedding extensions.vector,
 document_hash text NOT NULL,template_version text NOT NULL,status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','stale','failed')),
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),embedded_at timestamptz,
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count>=0),next_retry_at timestamptz,lease_token uuid,lease_until timestamptz,error_message text,
 PRIMARY KEY(category_id,model_id),CHECK(status<>'ready' OR (embedding IS NOT NULL AND embedded_at IS NOT NULL AND error_message IS NULL)));
UPDATE public.product_search_documents SET embedding_text=keyword_text,content_hash=encode(sha256(convert_to(keyword_text,'UTF8')),'hex');
ALTER TABLE public.product_search_documents ADD CHECK(content_hash=encode(sha256(convert_to(embedding_text,'UTF8')),'hex'));
INSERT INTO public.category_search_documents(category_id,embedding_text,source_hash,template_version)
 SELECT id,name_th,encode(extensions.digest(name_th,'sha256'),'hex'),'fixture-v1' FROM public.categories;
ALTER TABLE public.embedding_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.product_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.category_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.category_search_documents ENABLE ROW LEVEL SECURITY;
CREATE ROLE catalog_embedding_worker NOLOGIN;
CREATE ROLE embedding_worker LOGIN;
GRANT catalog_embedding_worker TO embedding_worker;

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
CREATE TRIGGER search_v2_category_embedding_guard BEFORE INSERT OR UPDATE ON public.category_embeddings FOR EACH ROW EXECUTE FUNCTION dataset_admin.validate_category_embedding();

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
CREATE TRIGGER search_v2_category_doc_stale AFTER UPDATE ON public.category_search_documents FOR EACH ROW EXECUTE FUNCTION dataset_admin.category_document_changed();

CREATE OR REPLACE FUNCTION dataset_admin.lock_used_model_configuration()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
begin
 if (new.provider,new.model_name,new.dimensions,new.distance_metric,new.settings) is distinct from
 (old.provider,old.model_name,old.dimensions,old.distance_metric,old.settings)
 and (exists(select 1 from public.category_embeddings where model_id=old.id)
 or exists(select 1 from public.product_embeddings where model_id=old.id))
 then raise exception 'Used model configuration is immutable; register a new configuration'; end if;
 return new;
end $function$;
CREATE TRIGGER search_v2_model_config_guard BEFORE UPDATE ON public.embedding_models FOR EACH ROW EXECUTE FUNCTION dataset_admin.lock_used_model_configuration();

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
CREATE TRIGGER embedding_valid BEFORE INSERT OR UPDATE ON public.product_embeddings FOR EACH ROW EXECUTE FUNCTION validate_embedding();

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
CREATE TRIGGER embedding_stale AFTER UPDATE ON public.product_search_documents FOR EACH ROW EXECUTE FUNCTION invalidate_embedding();

CREATE OR REPLACE FUNCTION public.touch_catalog_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$ begin new.updated_at=now();return new;end $function$;
CREATE TRIGGER touch_document BEFORE UPDATE ON public.product_search_documents FOR EACH ROW EXECUTE FUNCTION touch_catalog_updated_at();

-- Minimal independent schema: tests privileges/view inheritance, not product search relevance.
CREATE ROLE catalog_search_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE search_api LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE anon NOLOGIN;
GRANT catalog_search_reader TO search_api;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA extensions;
CREATE SCHEMA dataset_admin;
CREATE TABLE public.categories(id text PRIMARY KEY);
CREATE TABLE public.brands(id text PRIMARY KEY);
CREATE TABLE public.attribute_definitions(id text PRIMARY KEY);
CREATE TABLE public.category_attributes(id text PRIMARY KEY);
CREATE TABLE public.features(id text PRIMARY KEY);
CREATE TABLE public.category_features(id text PRIMARY KEY);
CREATE TABLE public.unit_conversion_rules(id text PRIMARY KEY);
CREATE TABLE public.products(id integer PRIMARY KEY,category_id text,status text,name_th text,model text);
CREATE TABLE public.product_attribute_values(product_id integer);
CREATE TABLE public.product_features(product_id integer);
CREATE TABLE public.product_search_documents(product_id integer);
CREATE TABLE public.product_content_overrides(product_id integer,section_type text,body text);
CREATE TABLE public.category_content_templates(category_id text,section_type text,body text);
CREATE TABLE public.category_search_documents(category_id text);
CREATE TABLE public.category_embeddings(category_id text);
CREATE TABLE public.product_embeddings(product_id integer);
CREATE TABLE public.embedding_models(id text);
CREATE TABLE dataset_admin.import_runs(id text);
CREATE TABLE dataset_admin.brand_category_evidence(id text);
DO $$DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['categories','brands','attribute_definitions','category_attributes',
    'features','category_features','unit_conversion_rules','products','product_attribute_values',
    'product_features','product_search_documents','product_content_overrides','category_content_templates',
    'category_search_documents','category_embeddings','product_embeddings','embedding_models'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  END LOOP;
END $$;
CREATE VIEW public.category_search_catalog WITH(security_invoker=true) AS SELECT * FROM public.categories;
CREATE VIEW public.category_attribute_search_catalog WITH(security_invoker=true) AS SELECT * FROM public.category_attributes;
CREATE VIEW public.product_content_sections WITH(security_invoker=true) AS
 SELECT product_id,section_type,body FROM public.product_content_overrides
 UNION ALL
 SELECT p.id,t.section_type,t.body FROM public.products p JOIN public.category_content_templates t ON t.category_id=p.category_id
 WHERE NOT EXISTS(SELECT 1 FROM public.product_content_overrides o WHERE o.product_id=p.id AND o.section_type=t.section_type);
CREATE FUNCTION public.search_products(p_query text DEFAULT '',p_category_id text DEFAULT NULL,
  p_brand_id text DEFAULT NULL,p_min_price numeric DEFAULT NULL,p_max_price numeric DEFAULT NULL,
  p_feature_ids text[] DEFAULT '{}',p_specs jsonb DEFAULT '[]',p_sort text DEFAULT 'relevance',
  p_limit integer DEFAULT 20,p_offset integer DEFAULT 0)
RETURNS TABLE(id integer) LANGUAGE sql STABLE SECURITY INVOKER AS
 'SELECT id FROM public.products WHERE status=''published'' AND (p_query='''' OR model=p_query) ORDER BY id LIMIT p_limit OFFSET p_offset';
REVOKE ALL ON FUNCTION public.search_products(text,text,text,numeric,numeric,text[],jsonb,text,integer,integer) FROM PUBLIC;
CREATE POLICY published_read ON public.products FOR SELECT TO anon USING(status='published');
GRANT USAGE ON SCHEMA public TO anon;
GRANT SELECT ON public.products TO anon;
INSERT INTO public.categories VALUES('TV0108'),('AP0101'),('KT0101');
INSERT INTO public.category_attributes VALUES('AT0048');
INSERT INTO public.unit_conversion_rules VALUES('inch-mm');
INSERT INTO public.products VALUES
 (100000,'TV0108','published','Public TV','TEST-TV'),
 (100001,'AP0101','published','Public washer','TEST-WASH'),
 (106045,'TV0108','draft','Secret TV','DRAFT-TV'),
 (106046,'KT0101','draft','Secret kitchen','DRAFT-KT');
INSERT INTO public.product_attribute_values SELECT id FROM public.products;
INSERT INTO public.product_features SELECT id FROM public.products;
INSERT INTO public.product_search_documents SELECT id FROM public.products;
INSERT INTO public.product_content_overrides VALUES(100000,'s1','Public override'),(106045,'s1','Private override');
INSERT INTO public.category_content_templates
 SELECT c.id,'s'||n,'Template for '||c.id FROM public.categories c CROSS JOIN generate_series(1,13) n;
-- Up Migration
-- Existing schema is the baseline. Never recreate catalog tables or replay old imports.
DO $preflight$
DECLARE t text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='catalog_search_reader'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb) THEN
    RAISE EXCEPTION 'Missing or unsafe catalog_search_reader role';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='search_api' AND rolcanlogin
      AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb) THEN
    RAISE EXCEPTION 'Missing or unsafe search_api role';
  END IF;
  IF NOT pg_has_role('search_api','catalog_search_reader','MEMBER') THEN
    RAISE EXCEPTION 'search_api must belong to catalog_search_reader';
  END IF;
  FOREACH t IN ARRAY ARRAY['categories','brands','attribute_definitions','category_attributes',
    'features','category_features','unit_conversion_rules','products','product_attribute_values',
    'product_features','product_search_documents','product_content_overrides','category_content_templates'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname=t AND c.relkind='r' AND c.relrowsecurity) THEN
      RAISE EXCEPTION 'Missing table or disabled RLS: public.%', t;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY ARRAY['category_search_catalog','category_attribute_search_catalog','product_content_sections'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname=t AND c.relkind='v'
      AND 'security_invoker=true'=ANY(coalesce(c.reloptions,'{}'))) THEN
      RAISE EXCEPTION 'Missing security_invoker view: public.%', t;
    END IF;
  END LOOP;
END $preflight$;

GRANT USAGE ON SCHEMA public, extensions TO catalog_search_reader;
GRANT SELECT ON public.categories, public.brands, public.attribute_definitions,
  public.category_attributes, public.features, public.category_features, public.unit_conversion_rules,
  public.products, public.product_attribute_values, public.product_features, public.product_search_documents,
  public.product_content_overrides, public.category_content_templates,
  public.category_search_catalog, public.category_attribute_search_catalog, public.product_content_sections
TO catalog_search_reader;
GRANT EXECUTE ON FUNCTION public.search_products(text,text,text,numeric,numeric,text[],jsonb,text,integer,integer)
TO catalog_search_reader;

DO $policies$
DECLARE t text; predicate text;
BEGIN
  FOREACH t IN ARRAY ARRAY['categories','brands','attribute_definitions','category_attributes',
    'features','category_features','unit_conversion_rules'] LOOP
    EXECUTE format('CREATE POLICY search_v2_reader_allow ON public.%I FOR SELECT TO catalog_search_reader USING (true)',t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['products','product_attribute_values','product_features',
    'product_search_documents','product_content_overrides','category_content_templates'] LOOP
    IF t='products' THEN
      predicate := 'status=''published''';
    ELSIF t='category_content_templates' THEN
      predicate := 'EXISTS (SELECT 1 FROM public.products p WHERE p.category_id=category_content_templates.category_id AND p.status=''published'')';
    ELSE
      predicate := format('EXISTS (SELECT 1 FROM public.products p WHERE p.id=%I.product_id AND p.status=''published'')',t);
    END IF;
    EXECUTE format('CREATE POLICY search_v2_reader_allow ON public.%I FOR SELECT TO catalog_search_reader USING (%s)',t,predicate);
    -- Restrictive guard prevents a later broad permissive policy from exposing drafts to this reader.
    EXECUTE format('CREATE POLICY search_v2_reader_published_guard ON public.%I AS RESTRICTIVE FOR SELECT TO catalog_search_reader USING (%s)',t,predicate);
  END LOOP;
END $policies$;

-- Down Migration
-- Revert only policies/grants introduced here. Keep data, roles and existing public policies.
DO $rollback$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['categories','brands','attribute_definitions','category_attributes',
    'features','category_features','unit_conversion_rules','products','product_attribute_values',
    'product_features','product_search_documents','product_content_overrides','category_content_templates'] LOOP
    EXECUTE format('DROP POLICY search_v2_reader_allow ON public.%I',t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['products','product_attribute_values','product_features',
    'product_search_documents','product_content_overrides','category_content_templates'] LOOP
    EXECUTE format('DROP POLICY search_v2_reader_published_guard ON public.%I',t);
  END LOOP;
END $rollback$;
REVOKE EXECUTE ON FUNCTION public.search_products(text,text,text,numeric,numeric,text[],jsonb,text,integer,integer)
FROM catalog_search_reader;
REVOKE SELECT ON public.categories, public.brands, public.attribute_definitions,
  public.category_attributes, public.features, public.category_features, public.unit_conversion_rules,
  public.products, public.product_attribute_values, public.product_features, public.product_search_documents,
  public.product_content_overrides, public.category_content_templates,
  public.category_search_catalog, public.category_attribute_search_catalog, public.product_content_sections
FROM catalog_search_reader;
REVOKE USAGE ON SCHEMA public, extensions FROM catalog_search_reader;

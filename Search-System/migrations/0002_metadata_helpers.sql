-- Up Migration
-- Existing aliases, metadata views and unit rules are reused without reseeding them.
CREATE SCHEMA catalog_search;
REVOKE ALL ON SCHEMA catalog_search FROM PUBLIC;
GRANT USAGE ON SCHEMA catalog_search TO catalog_search_reader;

CREATE FUNCTION catalog_search.normalize_label(p_label text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER SET search_path='' AS $$
  SELECT pg_catalog.lower(pg_catalog.btrim(pg_catalog.regexp_replace(p_label,'[[:space:]]+',' ','g')))
$$;

-- These functions return exact metadata candidates, not a selected/resolved product category.
CREATE FUNCTION catalog_search.category_label_matches(p_query text)
RETURNS TABLE(category_id text,parent_id text,level integer,name_th text,name_en text,aliases text[],match_source text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE q text := catalog_search.normalize_label(p_query);
BEGIN
  IF q IS NULL OR q='' OR pg_catalog.char_length(p_query)>200 THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid metadata label query';
  END IF;
  RETURN QUERY
  SELECT c.category_id,c.parent_id,c.level::integer,c.name_th,c.name_en,c.aliases,
    CASE WHEN catalog_search.normalize_label(c.category_id)=q THEN 'id'
      WHEN q IN (catalog_search.normalize_label(c.name_th),catalog_search.normalize_label(c.name_en)) THEN 'name'
      ELSE 'alias' END
  FROM public.category_search_catalog c
  WHERE EXISTS(SELECT 1 FROM pg_catalog.unnest(ARRAY[c.category_id,c.name_th,c.name_en]||c.aliases) l(label)
    WHERE catalog_search.normalize_label(l.label)=q)
  ORDER BY c.category_id;
END $$;

CREATE FUNCTION catalog_search.attribute_label_matches(p_category_id text,p_query text)
RETURNS TABLE(category_id text,attribute_id text,code text,name_th text,aliases text[],data_type text,
  unit_code text,unit_label text,is_required boolean,is_filterable boolean,
  min_value numeric,max_value numeric,display_order smallint,match_source text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE q text := catalog_search.normalize_label(p_query);
BEGIN
  IF p_category_id IS NULL OR p_category_id !~ '^[A-Z]{2}[0-9]{4}$' OR q IS NULL OR q=''
    OR pg_catalog.char_length(p_query)>200 THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid category or metadata label query';
  END IF;
  RETURN QUERY
  SELECT a.category_id,a.attribute_id,a.code,a.name_th,a.aliases,a.data_type,a.unit_code,a.unit_label,
    a.is_required,a.is_filterable,a.min_value,a.max_value,a.display_order,
    CASE WHEN catalog_search.normalize_label(a.attribute_id)=q THEN 'id'
      WHEN catalog_search.normalize_label(a.code)=q THEN 'code'
      WHEN catalog_search.normalize_label(a.name_th)=q THEN 'name' ELSE 'alias' END
  FROM public.category_attribute_search_catalog a
  WHERE a.category_id=p_category_id AND EXISTS(
    SELECT 1 FROM pg_catalog.unnest(ARRAY[a.attribute_id,a.code,a.name_th]||a.aliases) l(label)
    WHERE catalog_search.normalize_label(l.label)=q)
  ORDER BY a.display_order,a.attribute_id;
END $$;

CREATE FUNCTION catalog_search.convert_unit(p_value numeric,p_from_unit text,p_to_unit text)
RETURNS TABLE(status text,value_out numeric,from_unit text,to_unit text,dimension text,rule_version text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE src text:=pg_catalog.btrim(p_from_unit); dst text:=pg_catalog.btrim(p_to_unit);
  rule public.unit_conversion_rules%ROWTYPE;
BEGIN
  IF p_value IS NULL OR p_value::text IN ('NaN','Infinity','-Infinity') THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Unit conversion requires a finite numeric value';
  END IF;
  IF src IS NULL OR src='' OR dst IS NULL OR dst='' THEN
    RETURN QUERY SELECT 'missing_unit'::text,NULL::numeric,src,dst,NULL::text,NULL::text; RETURN;
  END IF;
  IF pg_catalog.char_length(src)>32 OR pg_catalog.char_length(dst)>32 THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Unit label is too long';
  END IF;
  -- Unit symbols are case-sensitive. Never run normalize_label/fuzzy/transitive matching here.
  SELECT r.* INTO rule FROM public.unit_conversion_rules r WHERE r.from_unit=src AND r.to_unit=dst;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'unsupported_unit'::text,NULL::numeric,src,dst,NULL::text,NULL::text; RETURN;
  END IF;
  IF NOT rule.enabled THEN
    RETURN QUERY SELECT 'disabled_rule'::text,NULL::numeric,src,dst,rule.dimension,rule.rule_version; RETURN;
  END IF;
  IF rule.multiplier IS NULL OR rule.offset_value IS NULL OR rule.multiplier<=0 OR rule.multiplier::text IN ('NaN','Infinity','-Infinity')
    OR rule.offset_value::text IN ('NaN','Infinity','-Infinity')
    OR (src=dst AND (rule.multiplier<>1 OR rule.offset_value<>0)) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid stored unit rule';
  END IF;
  RETURN QUERY SELECT CASE WHEN src=dst THEN 'unchanged' ELSE 'converted' END,
    p_value*rule.multiplier+rule.offset_value,src,dst,rule.dimension,rule.rule_version;
END $$;

CREATE FUNCTION catalog_search.metadata_version() RETURNS text
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
  SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    'metadata-v1' || coalesce((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) ORDER BY c.category_id)::text FROM public.category_search_catalog c),'[]')
    || coalesce((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(a) ORDER BY a.category_id,a.attribute_id)::text FROM public.category_attribute_search_catalog a),'[]')
    || coalesce((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
      'from_unit',u.from_unit,'to_unit',u.to_unit,'dimension',u.dimension,'multiplier',u.multiplier,
      'offset_value',u.offset_value,'enabled',u.enabled,'rule_version',u.rule_version)
      ORDER BY u.from_unit,u.to_unit)::text FROM public.unit_conversion_rules u),'[]'), 'UTF8')),'hex')
$$;

CREATE FUNCTION catalog_search.metadata_audit() RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
  WITH category_labels AS (
    SELECT c.category_id,catalog_search.normalize_label(l.label) AS label
    FROM public.category_search_catalog c CROSS JOIN LATERAL pg_catalog.unnest(ARRAY[c.category_id,c.name_th,c.name_en]||c.aliases) l(label)
  ), category_collisions AS (
    SELECT label,pg_catalog.array_agg(DISTINCT category_id ORDER BY category_id) AS ids
    FROM category_labels WHERE label IS NOT NULL AND label<>'' GROUP BY label HAVING pg_catalog.count(DISTINCT category_id)>1
  ), attribute_labels AS (
    SELECT a.category_id,a.attribute_id,catalog_search.normalize_label(l.label) AS label
    FROM public.category_attribute_search_catalog a CROSS JOIN LATERAL pg_catalog.unnest(ARRAY[a.attribute_id,a.code,a.name_th]||a.aliases) l(label)
  ), attribute_collisions AS (
    SELECT category_id,label,pg_catalog.array_agg(DISTINCT attribute_id ORDER BY attribute_id) AS ids
    FROM attribute_labels WHERE label IS NOT NULL AND label<>'' GROUP BY category_id,label HAVING pg_catalog.count(DISTINCT attribute_id)>1
  )
  SELECT pg_catalog.jsonb_build_object('version',catalog_search.metadata_version(),
    'categories',(SELECT pg_catalog.count(*) FROM public.categories),
    'attributes',(SELECT pg_catalog.count(*) FROM public.attribute_definitions),
    'category_attributes',(SELECT pg_catalog.count(*) FROM public.category_attribute_search_catalog),
    'enabled_unit_rules',(SELECT pg_catalog.count(*) FROM public.unit_conversion_rules WHERE enabled),
    'categories_without_aliases',(SELECT pg_catalog.count(*) FROM public.categories WHERE coalesce(pg_catalog.cardinality(aliases),0)=0),
    'attributes_without_aliases',(SELECT pg_catalog.count(*) FROM public.attribute_definitions WHERE coalesce(pg_catalog.cardinality(aliases),0)=0),
    'category_collision_count',(SELECT pg_catalog.count(*) FROM category_collisions),
    'attribute_collision_count',(SELECT pg_catalog.count(*) FROM attribute_collisions),
    'category_collision_examples',coalesce((SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM category_collisions ORDER BY label LIMIT 10)x),'[]'::jsonb),
    'attribute_collision_examples',coalesce((SELECT pg_catalog.jsonb_agg(x) FROM (SELECT * FROM attribute_collisions ORDER BY category_id,label LIMIT 10)x),'[]'::jsonb),
    'unit_codes_without_rules',coalesce((SELECT pg_catalog.jsonb_agg(x.unit_code ORDER BY x.unit_code) FROM (
      SELECT DISTINCT a.unit_code FROM public.attribute_definitions a WHERE a.unit_code IS NOT NULL
      AND NOT EXISTS(SELECT 1 FROM public.unit_conversion_rules r WHERE r.enabled AND (r.from_unit=a.unit_code OR r.to_unit=a.unit_code)))x),'[]'::jsonb))
$$;

REVOKE ALL ON FUNCTION catalog_search.normalize_label(text),catalog_search.category_label_matches(text),
  catalog_search.attribute_label_matches(text,text),catalog_search.convert_unit(numeric,text,text),
  catalog_search.metadata_version(),catalog_search.metadata_audit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog_search.normalize_label(text),catalog_search.category_label_matches(text),
  catalog_search.attribute_label_matches(text,text),catalog_search.convert_unit(numeric,text,text),
  catalog_search.metadata_version(),catalog_search.metadata_audit() TO catalog_search_reader;

-- Down Migration
-- No existing metadata/aliases/rules/products are removed. No CASCADE into later resolver functions.
DROP FUNCTION catalog_search.metadata_audit();
DROP FUNCTION catalog_search.metadata_version();
DROP FUNCTION catalog_search.convert_unit(numeric,text,text);
DROP FUNCTION catalog_search.attribute_label_matches(text,text);
DROP FUNCTION catalog_search.category_label_matches(text);
DROP FUNCTION catalog_search.normalize_label(text);
REVOKE USAGE ON SCHEMA catalog_search FROM catalog_search_reader;
DROP SCHEMA catalog_search;

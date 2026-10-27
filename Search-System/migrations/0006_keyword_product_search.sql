-- Up Migration
-- Add SQL search only. Existing public.search_products, storage, products and indexes remain unchanged.
CREATE FUNCTION catalog_search.validate_keyword_request(p_request jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE k text; v jsonb; n numeric;
BEGIN
 IF p_request IS NULL OR pg_catalog.jsonb_typeof(p_request)<>'object' THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected search request object';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_catalog.jsonb_object_keys(p_request) x WHERE x NOT IN
   ('version','query','semantic_query','filters','exact','sort','page','limit')) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Unknown search field';
 END IF;
 IF p_request ? 'version' AND p_request->'version'<>'"1"'::jsonb THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid version'; END IF;
 FOREACH k IN ARRAY ARRAY['query','semantic_query'] LOOP
  IF p_request ? k THEN
   v:=p_request->k;
   IF pg_catalog.jsonb_typeof(v)<>'string' OR pg_catalog.char_length(v#>>'{}')>
     (CASE WHEN k='query' THEN 200 ELSE 2000 END) OR (k='semantic_query' AND pg_catalog.btrim(v#>>'{}')='') THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid query text';
   END IF;
  END IF;
 END LOOP;
 PERFORM catalog_search.validate_filter_input_v2(coalesce(p_request->'filters','{}'));
 IF p_request ? 'sort' AND (pg_catalog.jsonb_typeof(p_request->'sort')<>'string' OR p_request->>'sort' NOT IN
   ('relevance','price_asc','price_desc','id')) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid sort'; END IF;
 FOREACH k IN ARRAY ARRAY['page','limit'] LOOP
  IF p_request ? k THEN
   IF pg_catalog.jsonb_typeof(p_request->k)<>'number' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected integer pagination'; END IF;
   n:=(p_request->>k)::numeric;
   IF n<>pg_catalog.trunc(n) OR n<1 OR n>(CASE WHEN k='page' THEN 10001 ELSE 100 END) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Pagination out of range';
   END IF;
  END IF;
 END LOOP;
 IF (coalesce((p_request->>'page')::integer,1)-1)*coalesce((p_request->>'limit')::integer,20)>10000 THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Offset out of range';
 END IF;
 IF p_request ? 'exact' THEN
  v:=p_request->'exact';
  IF pg_catalog.jsonb_typeof(v)<>'object' OR v='{}'::jsonb THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid identity selector'; END IF;
  IF EXISTS(SELECT 1 FROM pg_catalog.jsonb_object_keys(v) x WHERE x NOT IN ('article_id','model')) THEN
   RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Unknown identity field';
  END IF;
  IF v ? 'article_id' THEN
   IF pg_catalog.jsonb_typeof(v->'article_id')<>'number' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected integer Article'; END IF;
   n:=(v->>'article_id')::numeric;
   IF n<>pg_catalog.trunc(n) OR n<100000 OR n>199999 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid Article'; END IF;
  END IF;
  IF v ? 'model' AND (pg_catalog.jsonb_typeof(v->'model')<>'string' OR pg_catalog.btrim(v->>'model')=''
   OR pg_catalog.char_length(v->>'model')>100) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid model'; END IF;
 END IF;
END $$;

CREATE FUNCTION catalog_search.search_products_keyword(p_category_ids text[],p_request jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE filters jsonb; resolution jsonb; q text; raw_query text; tokens text[]; exact_id integer; query_id integer;
 exact_model text; query_model text; page_number integer; page_size integer; offset_rows integer; sort_mode text;
 answer jsonb; warnings jsonb:='[{"code":"semantic_unavailable","message":"Keyword search only; semantic_query has not been used."}]';
 has_filters boolean;
BEGIN
 PERFORM catalog_search.validate_keyword_request(p_request);
 filters:=coalesce(p_request->'filters','{}');
 raw_query:=pg_catalog.btrim(coalesce(p_request->>'query',''));q:=pg_catalog.lower(raw_query);
 page_number:=coalesce((p_request->>'page')::integer,1);page_size:=coalesce((p_request->>'limit')::integer,20);
 offset_rows:=(page_number-1)*page_size;sort_mode:=coalesce(p_request->>'sort','relevance');
 has_filters:=filters ? 'category_query' OR filters ? 'category_ids'
  OR pg_catalog.jsonb_array_length(coalesce(filters->'brand_queries','[]'))>0
  OR pg_catalog.jsonb_array_length(coalesce(filters->'feature_queries_all','[]'))>0
  OR pg_catalog.jsonb_array_length(coalesce(filters->'feature_ids_all','[]'))>0
  OR pg_catalog.jsonb_array_length(coalesce(filters->'specs','[]'))>0
  OR filters#>>'{price,min}' IS NOT NULL OR filters#>>'{price,max}' IS NOT NULL;
 IF q='' AND NOT has_filters AND NOT(p_request ? 'exact') THEN
  IF p_request ? 'semantic_query' THEN
   RETURN pg_catalog.jsonb_build_object('engine_version','keyword-v2','status','needs_clarification','semantic_available',false,
    'warnings',warnings,'issues',pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/semantic_query',
     'reason','semantic_unavailable_and_no_keyword_constraint')),'results','[]'::jsonb);
  END IF;
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Empty search';
 END IF;
 -- Recompute typed metadata in this SQL statement: caller cannot invent applied evidence.
 resolution:=catalog_search.resolve_typed_filters_v2(p_category_ids,filters);
 IF resolution->>'status'<>'resolved' THEN
  RETURN pg_catalog.jsonb_build_object('engine_version','keyword-v2','status',resolution->>'status','semantic_available',false,
   'warnings',warnings,'filter_resolution',resolution,'results','[]'::jsonb);
 END IF;
 exact_id:=(p_request#>>'{exact,article_id}')::integer;exact_model:=p_request#>>'{exact,model}';
 IF raw_query ~ '^1[0-9]{5}$' THEN query_id:=raw_query::integer; END IF;
 IF raw_query ~ '^[A-Z]{2}[0-9]{4}-[0-9]{3,}$' OR
  (q<>'' AND EXISTS(SELECT 1 FROM public.products p WHERE p.status='published' AND p.model=raw_query)) THEN query_model:=raw_query; END IF;
 IF (exact_id IS NOT NULL AND query_id IS NOT NULL AND exact_id<>query_id)
  OR (exact_model IS NOT NULL AND query_model IS NOT NULL AND exact_model<>query_model)
  OR (coalesce(exact_id,query_id) IS NOT NULL AND coalesce(exact_model,query_model) IS NOT NULL AND EXISTS(SELECT 1 FROM public.products p
    WHERE p.status='published' AND ((p.id=coalesce(exact_id,query_id) AND p.model<>coalesce(exact_model,query_model))
      OR (p.model=coalesce(exact_model,query_model) AND p.id<>coalesce(exact_id,query_id))))) THEN
  RETURN pg_catalog.jsonb_build_object('engine_version','keyword-v2','status','constraint_conflict','semantic_available',false,
   'warnings',warnings,'issues',pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/exact','reason','identity_selectors_conflict')),
   'filter_resolution',resolution,'results','[]'::jsonb);
 END IF;
 tokens:=pg_catalog.regexp_split_to_array(q,'[[:space:]]+');
 WITH branches AS MATERIALIZED (
  SELECT b FROM pg_catalog.jsonb_array_elements(resolution->'branches') b WHERE b->>'status' IN ('ready','partial')
 ), scoped AS MATERIALIZED (
  SELECT p.id,p.category_id,p.brand_id,p.model,p.name_th,p.price,p.is_mock,p.mock_notice,b.b,
   pg_catalog.lower(pg_catalog.concat_ws(' ',p.id::text,p.model,p.name_th,p.name_en,br.name,br.name_th,br.aliases::text,
    c.name_th,c.name_en,pg_catalog.array_to_string(c.aliases,' '),cp.name_th,cp.name_en,pg_catalog.array_to_string(cp.aliases,' '),
    cg.name_th,cg.name_en,pg_catalog.array_to_string(cg.aliases,' '))) AS core,
   coalesce(d.keyword_text,'') AS document
  FROM branches b JOIN public.products p ON p.category_id=b.b->>'category_id'
   JOIN public.brands br ON br.id=p.brand_id JOIN public.categories c ON c.id=p.category_id
   LEFT JOIN public.categories cp ON cp.id=c.parent_id LEFT JOIN public.categories cg ON cg.id=cp.parent_id
   LEFT JOIN public.product_search_documents d ON d.product_id=p.id
  WHERE p.status='published' AND (exact_id IS NULL OR p.id=exact_id) AND (query_id IS NULL OR p.id=query_id)
   AND (exact_model IS NULL OR p.model=exact_model) AND (query_model IS NULL OR p.model=query_model)
 ), lexical AS MATERIALIZED (
  SELECT p.*,CASE WHEN q='' THEN true ELSE
   NOT EXISTS(SELECT 1 FROM pg_catalog.unnest(tokens) t WHERE pg_catalog.strpos(p.core||' '||pg_catalog.lower(p.document),t)=0)
   OR pg_catalog.to_tsvector('pg_catalog.simple'::regconfig,p.core) @@ pg_catalog.plainto_tsquery('pg_catalog.simple'::regconfig,q) END AS keyword_match,
   CASE WHEN q='' THEN 0::real ELSE
    (CASE WHEN p.model=raw_query OR p.id::text=raw_query THEN 1000 ELSE 0 END
      + CASE WHEN pg_catalog.lower(p.name_th)=q THEN 500 ELSE 0 END
      + CASE WHEN pg_catalog.strpos(pg_catalog.lower(p.name_th),q)>0 THEN 100 ELSE 0 END
      + CASE WHEN pg_catalog.strpos(p.core,q)>0 THEN 20 ELSE 0 END)::real
     + pg_catalog.ts_rank_cd(pg_catalog.to_tsvector('pg_catalog.simple'::regconfig,p.core),pg_catalog.plainto_tsquery('pg_catalog.simple'::regconfig,q)) END AS keyword_score
  FROM scoped p
 ), spec_checks AS (
  SELECT p.id,s.s,v.value_number,v.value_text,v.value_boolean,
   CASE WHEN v.product_id IS NULL OR v.data_type IS DISTINCT FROM (s.s->>'data_type')
     OR (s.s->>'data_type'='number' AND (v.value_number IS NULL OR v.value_number::text IN ('NaN','Infinity','-Infinity')))
     OR (s.s->>'data_type'='text' AND v.value_text IS NULL)
     OR (s.s->>'data_type'='boolean' AND v.value_boolean IS NULL) THEN 'unknown'
    WHEN s.s->>'data_type'='number' THEN CASE WHEN
      (s.s->>'op'='eq' AND v.value_number BETWEEN (s.s->>'numeric_value')::numeric-(s.s->>'comparison_epsilon')::numeric AND (s.s->>'numeric_value')::numeric+(s.s->>'comparison_epsilon')::numeric)
      OR (s.s->>'op'='gte' AND v.value_number>=(s.s->>'numeric_value')::numeric-(s.s->>'comparison_epsilon')::numeric)
      OR (s.s->>'op'='lte' AND v.value_number<=(s.s->>'numeric_value')::numeric+(s.s->>'comparison_epsilon')::numeric) THEN 'met' ELSE 'unmet' END
    WHEN s.s->>'data_type'='text' THEN CASE WHEN v.value_text=s.s->>'text_value' THEN 'met' ELSE 'unmet' END
    ELSE CASE WHEN v.value_boolean=(s.s->>'boolean_value')::boolean THEN 'met' ELSE 'unmet' END END AS state
  FROM lexical p CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(p.b->'resolved_specs') s(s)
   LEFT JOIN public.product_attribute_values v ON v.product_id=p.id AND v.category_id=p.category_id AND v.attribute_id=s.s->>'attribute_id'
 ), differences AS (
  SELECT s.id,s.state,'attribute:'||(s.s->>'attribute_id') AS constraint_key,
   pg_catalog.jsonb_build_object('path',s.s->>'path','attribute_id',s.s->>'attribute_id',
    'requested_value',CASE WHEN s.s->>'data_type'='number' THEN pg_catalog.to_jsonb((s.s->>'numeric_value')::numeric)
      WHEN s.s->>'data_type'='boolean' THEN pg_catalog.to_jsonb((s.s->>'boolean_value')::boolean) ELSE s.s->'text_value' END,
    'actual_value',CASE WHEN s.state='unknown' THEN 'null'::jsonb WHEN s.s->>'data_type'='number' THEN pg_catalog.to_jsonb(s.value_number)
      WHEN s.s->>'data_type'='boolean' THEN pg_catalog.to_jsonb(s.value_boolean) ELSE pg_catalog.to_jsonb(s.value_text) END,
    'canonical_unit',s.s->'unit_code','requested_numeric_decimal',s.s->'numeric_value',
    'actual_numeric_decimal',CASE WHEN s.state<>'unknown' THEN s.value_number::text END,
    'input_value',s.s->'requested_value','input_unit',s.s->'requested_unit',
    'reason',CASE WHEN s.state='unknown' THEN 'attribute_value_missing_or_invalid' ELSE 'attribute_value_mismatch' END) AS diagnostic
  FROM spec_checks s WHERE s.state<>'met'
  UNION ALL
  SELECT p.id,'unknown','feature:'||(f.f->>'feature_id'),pg_catalog.jsonb_build_object('path',f.f->>'path',
   'requested_value',f.f->'feature_id','actual_value','null'::jsonb,'reason','feature_not_recorded')
  FROM lexical p CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(p.b->'resolved_features') f(f)
  WHERE NOT EXISTS(SELECT 1 FROM public.product_features pf WHERE pf.product_id=p.id AND pf.category_id=p.category_id AND pf.feature_id=f.f->>'feature_id')
  UNION ALL
  SELECT p.id,'unmet','brand',pg_catalog.jsonb_build_object('path','/filters/brand_queries','requested_value',p.b->'brand_ids',
   'actual_value',p.brand_id,'reason','brand_not_selected')
  FROM lexical p WHERE pg_catalog.jsonb_array_length(p.b->'brand_ids')>0
   AND NOT EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements_text(p.b->'brand_ids') selected_brand(value) WHERE selected_brand.value=p.brand_id)
  UNION ALL
  SELECT p.id,CASE WHEN p.price IS NULL OR p.price::text IN ('NaN','Infinity','-Infinity') THEN 'unknown' ELSE 'unmet' END,'price',
   pg_catalog.jsonb_build_object('path','/filters/price/'||bound.k,'requested_value',pg_catalog.to_jsonb(bound.n),
    'actual_value',CASE WHEN p.price IS NULL OR p.price::text IN ('NaN','Infinity','-Infinity') THEN 'null'::jsonb ELSE pg_catalog.to_jsonb(p.price) END,
    'canonical_unit','THB','requested_numeric_decimal',bound.n::text,'actual_numeric_decimal',CASE WHEN p.price::text NOT IN ('NaN','Infinity','-Infinity') THEN p.price::text END,
    'reason',CASE WHEN p.price IS NULL OR p.price::text IN ('NaN','Infinity','-Infinity') THEN 'price_unknown' ELSE 'price_outside_requested_range' END)
  FROM lexical p CROSS JOIN LATERAL (VALUES ('min'::text,(p.b#>>'{price,min}')::numeric),('max'::text,(p.b#>>'{price,max}')::numeric)) bound(k,n)
  WHERE bound.n IS NOT NULL AND (p.price IS NULL OR p.price::text IN ('NaN','Infinity','-Infinity')
    OR (bound.k='min' AND p.price<bound.n) OR (bound.k='max' AND p.price>bound.n))
  UNION ALL
  SELECT p.id,'unmet','query',pg_catalog.jsonb_build_object('path','/query','requested_value',raw_query,
   'actual_value',p.name_th,'reason','keyword_not_matched') FROM lexical p WHERE NOT p.keyword_match
 ), diagnostics AS MATERIALIZED (
  SELECT d.id,pg_catalog.count(DISTINCT d.constraint_key)::integer AS difference_count,
   pg_catalog.count(DISTINCT d.constraint_key) FILTER(WHERE d.state='unknown')::integer AS unknown_count,
   coalesce(max(CASE WHEN d.state='unmet' AND d.diagnostic->>'requested_numeric_decimal' IS NOT NULL
      AND d.diagnostic->>'actual_numeric_decimal' IS NOT NULL THEN
     pg_catalog.abs((d.diagnostic->>'actual_numeric_decimal')::numeric-(d.diagnostic->>'requested_numeric_decimal')::numeric)
       /greatest(pg_catalog.abs((d.diagnostic->>'actual_numeric_decimal')::numeric),pg_catalog.abs((d.diagnostic->>'requested_numeric_decimal')::numeric),1) END),0) AS numeric_distance,
   coalesce(pg_catalog.jsonb_agg(d.diagnostic ORDER BY d.diagnostic->>'path') FILTER(WHERE d.state='unmet'),'[]') AS unmet,
   coalesce(pg_catalog.jsonb_agg(d.diagnostic ORDER BY d.diagnostic->>'path') FILTER(WHERE d.state='unknown'),'[]') AS unknown
  FROM differences d GROUP BY d.id
 ), classified AS MATERIALIZED (
  SELECT p.*,coalesce(d.difference_count,0) AS difference_count,coalesce(d.unknown_count,0) AS unknown_count,coalesce(d.numeric_distance,0) AS numeric_distance,coalesce(d.unmet,'[]') AS unmet,coalesce(d.unknown,'[]') AS unknown,
   CASE WHEN d.id IS NULL THEN 0 ELSE 1 END AS tier FROM lexical p LEFT JOIN diagnostics d ON d.id=p.id
 ), totals AS (
  SELECT pg_catalog.count(*) FILTER(WHERE tier=0)::integer AS matches,pg_catalog.count(*) FILTER(WHERE tier=1)::integer AS alternatives FROM classified
 ), numbered AS (
  SELECT c.*,pg_catalog.row_number() OVER(ORDER BY c.tier,
   CASE WHEN sort_mode='relevance' THEN c.difference_count END,
   CASE WHEN sort_mode='relevance' THEN c.unknown_count END,
   CASE WHEN sort_mode='relevance' THEN c.numeric_distance END,
   CASE WHEN sort_mode='relevance' THEN c.keyword_score END DESC,
   CASE WHEN sort_mode='price_asc' THEN c.price END ASC NULLS LAST,
   CASE WHEN sort_mode='price_desc' THEN c.price END DESC NULLS LAST,c.id) AS position
  FROM classified c
 ), page_rows AS (
  SELECT * FROM numbered WHERE position>offset_rows AND position<=offset_rows+page_size
 )
 SELECT pg_catalog.jsonb_build_object('engine_version','keyword-v2','status',CASE WHEN t.matches+t.alternatives=0 THEN 'no_matches' ELSE 'ok' END,
  'semantic_available',false,'warnings',warnings,'filter_resolution',resolution,
  'resolved_categories',(SELECT coalesce(pg_catalog.jsonb_agg(b->>'category_id' ORDER BY b->>'category_id'),'[]') FROM branches),
  'meta',pg_catalog.jsonb_build_object('page',page_number,'limit',page_size,'total_matches',t.matches,'total_alternatives',t.alternatives,
   'returned_count',(SELECT pg_catalog.count(*) FROM page_rows),'has_more',offset_rows+(SELECT pg_catalog.count(*) FROM page_rows)<t.matches+t.alternatives,
   'all_requested_filters_applied',resolution->'all_requested_filters_applied'),
  'results',(SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('article_id',r.id,'category_id',r.category_id,
    'is_mock',r.is_mock,'mock_notice',r.mock_notice,'match_type',CASE WHEN r.tier=0 THEN 'matches_applied_filters' ELSE 'alternative' END,
    'keyword_score',r.keyword_score,'difference_count',r.difference_count,'unknown_count',r.unknown_count,'numeric_distance',r.numeric_distance::real,'unmet_filters',r.unmet,'unknown_filters',r.unknown) ORDER BY r.position),'[]') FROM page_rows r))
 INTO answer FROM totals t;
 RETURN answer;
END $$;

REVOKE ALL ON FUNCTION catalog_search.validate_keyword_request(jsonb),catalog_search.search_products_keyword(text[],jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog_search.validate_keyword_request(jsonb),catalog_search.search_products_keyword(text[],jsonb) TO catalog_search_reader;

-- Down Migration
DROP FUNCTION catalog_search.search_products_keyword(text[],jsonb);
DROP FUNCTION catalog_search.validate_keyword_request(jsonb);

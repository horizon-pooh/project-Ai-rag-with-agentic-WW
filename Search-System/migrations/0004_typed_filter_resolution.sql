-- Up Migration
-- No seeds, new storage, product scans or changes to applied migrations.
CREATE FUNCTION catalog_search.filter_name_candidates(p_kind text,p_query text,p_category_id text DEFAULT NULL)
RETURNS TABLE(id text,label text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE q text:=catalog_search.normalize_label(p_query);
BEGIN
  IF q IS NULL OR q='' OR pg_catalog.char_length(p_query)>200 OR p_kind NOT IN ('brand','feature') OR p_kind IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid named filter';
  END IF;
  IF p_kind='brand' THEN
    RETURN QUERY SELECT b.id,b.name FROM public.brands b WHERE
      CASE WHEN q ~ '^br[0-9]{4}$' THEN pg_catalog.lower(b.id)=q ELSE
        EXISTS(SELECT 1 FROM pg_catalog.unnest(ARRAY[b.name,b.name_th]) n WHERE catalog_search.normalize_label(n)=q)
        OR EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements_text(b.aliases) a WHERE catalog_search.normalize_label(a)=q) END
      ORDER BY b.id;
  ELSE
    IF p_category_id IS NULL OR p_category_id !~ '^[A-Z]{2}[0-9]{4}$' THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Feature lookup requires category';
    END IF;
    RETURN QUERY SELECT f.id,f.name_th FROM public.features f JOIN public.category_features cf ON cf.feature_id=f.id
    WHERE cf.category_id=p_category_id AND
      CASE WHEN q ~ '^ft[0-9]{4}$' THEN pg_catalog.lower(f.id)=q ELSE
        EXISTS(SELECT 1 FROM pg_catalog.unnest(ARRAY[f.code,f.name_th,f.name_en]) n WHERE catalog_search.normalize_label(n)=q)
        OR EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements_text(f.aliases) a WHERE catalog_search.normalize_label(a)=q) END
      ORDER BY f.id;
  END IF;
END $$;

-- Independently validate the JSON boundary; never cast a numeric-looking string.
CREATE FUNCTION catalog_search.validate_filter_input(p_filters jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE k text; a jsonb; s jsonb; v jsonb;
BEGIN
  IF p_filters IS NULL OR pg_catalog.jsonb_typeof(p_filters)<>'object' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected filters object';
  END IF;
  FOR k IN SELECT pg_catalog.jsonb_object_keys(p_filters) LOOP
    IF k NOT IN ('category_query','brand_queries','feature_queries_all','specs','price') THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Unknown filter key';
    END IF;
  END LOOP;
  IF p_filters ? 'category_query' AND (pg_catalog.jsonb_typeof(p_filters->'category_query')<>'string'
    OR pg_catalog.btrim(p_filters->>'category_query')='' OR pg_catalog.char_length(p_filters->>'category_query')>200) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid category label';
  END IF;
  FOREACH k IN ARRAY ARRAY['brand_queries','feature_queries_all','specs'] LOOP
    IF p_filters ? k THEN
      a:=p_filters->k;
      IF pg_catalog.jsonb_typeof(a)<>'array' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected filter array'; END IF;
      IF pg_catalog.jsonb_array_length(a)>20 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Too many filters'; END IF;
      IF k<>'specs' AND EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(a) x WHERE
        pg_catalog.jsonb_typeof(x)<>'string' OR pg_catalog.btrim(x#>>'{}')='' OR pg_catalog.char_length(x#>>'{}')>200) THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid named filter label';
      END IF;
    END IF;
  END LOOP;
  FOR s IN SELECT * FROM pg_catalog.jsonb_array_elements(coalesce(p_filters->'specs','[]')) LOOP
    IF pg_catalog.jsonb_typeof(s)<>'object' OR NOT(s ?& ARRAY['attribute_query','op','value']) THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid spec shape';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_catalog.jsonb_object_keys(s) x WHERE x NOT IN ('attribute_query','op','value','unit'))
      OR pg_catalog.jsonb_typeof(s->'attribute_query')<>'string' OR pg_catalog.btrim(s->>'attribute_query')=''
      OR pg_catalog.char_length(s->>'attribute_query')>200 OR pg_catalog.jsonb_typeof(s->'op')<>'string'
      OR s->>'op' NOT IN ('eq','gte','lte') OR pg_catalog.jsonb_typeof(s->'value') NOT IN ('number','string','boolean') THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid typed spec';
    END IF;
    IF pg_catalog.jsonb_typeof(s->'value')<>'number' AND (s->>'op'<>'eq' OR s ? 'unit') THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Text/boolean specs require equality and no unit';
    END IF;
    IF pg_catalog.jsonb_typeof(s->'value')='string' AND
      (pg_catalog.btrim(s->>'value')='' OR pg_catalog.char_length(s->>'value')>200) THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid text value';
    END IF;
    IF s ? 'unit' AND (pg_catalog.jsonb_typeof(s->'unit')<>'string' OR pg_catalog.btrim(s->>'unit')=''
      OR pg_catalog.char_length(s->>'unit')>32) THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid unit';
    END IF;
  END LOOP;
  IF p_filters ? 'price' THEN
    v:=p_filters->'price';
    IF pg_catalog.jsonb_typeof(v)<>'object' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid price'; END IF;
    IF EXISTS(SELECT 1 FROM pg_catalog.jsonb_object_keys(v) x WHERE x NOT IN ('min','max','currency'))
      OR (v ? 'currency' AND v->'currency'<>'"THB"'::jsonb) THEN
      RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid price fields';
    END IF;
    FOREACH k IN ARRAY ARRAY['min','max'] LOOP
      IF v ? k AND v->k<>'null'::jsonb THEN
        IF pg_catalog.jsonb_typeof(v->k)<>'number' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected numeric price'; END IF;
        IF (v->>k)::numeric<0 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Negative price'; END IF;
      END IF;
    END LOOP;
    IF (v->>'min')::numeric>(v->>'max')::numeric THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Inverted price'; END IF;
  END IF;
END $$;

-- Resolve one already validated constraint inside one leaf category.
CREATE FUNCTION catalog_search.resolve_spec(p_category_id text,p_spec jsonb,p_index integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE candidates jsonb; a record; n numeric; epsilon numeric:=0; conversion record;
  unit_mode text:='unitless'; requested_unit text:=pg_catalog.btrim(p_spec->>'unit'); reason text;
  path text:='/filters/specs/'||p_index; canonical text; rule_version text;
BEGIN
  PERFORM catalog_search.validate_filter_input(pg_catalog.jsonb_build_object('specs',pg_catalog.jsonb_build_array(p_spec)));
  IF p_index IS NULL OR p_index<0 OR p_index>19 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid spec position'; END IF;
  SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) ORDER BY c.attribute_id),'[]') INTO candidates
    FROM catalog_search.attribute_label_matches(p_category_id,p_spec->>'attribute_query') c
    WHERE CASE WHEN catalog_search.normalize_label(p_spec->>'attribute_query') ~ '^at[0-9]{4}$'
      THEN pg_catalog.lower(c.attribute_id)=catalog_search.normalize_label(p_spec->>'attribute_query') ELSE true END;
  IF pg_catalog.jsonb_array_length(candidates)<>1 THEN
    RETURN pg_catalog.jsonb_build_object('status','unapplied','path',path,
      'reason',CASE WHEN pg_catalog.jsonb_array_length(candidates)=0 THEN 'attribute_not_in_category' ELSE 'ambiguous_attribute' END,
      'candidates',(SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('id',x->>'attribute_id','label',x->>'name_th')),'[]') FROM pg_catalog.jsonb_array_elements(candidates) x));
  END IF;
  SELECT * INTO a FROM pg_catalog.jsonb_to_record(candidates->0) AS x(attribute_id text,data_type text,unit_code text,
    unit_label text,is_filterable boolean,min_value numeric,max_value numeric);
  IF NOT a.is_filterable THEN reason:='attribute_not_filterable';
  ELSIF (CASE WHEN a.data_type='text' THEN 'string' ELSE a.data_type END)<>pg_catalog.jsonb_typeof(p_spec->'value') THEN reason:='data_type_mismatch'; END IF;
  IF reason IS NOT NULL THEN RETURN pg_catalog.jsonb_build_object('status','unapplied','path',path,'reason',reason,'candidates','[]'::jsonb); END IF;
  canonical:=a.unit_code;
  IF a.data_type='number' THEN
    n:=(p_spec->>'value')::numeric;
    IF requested_unit IS NULL THEN unit_mode:=CASE WHEN canonical IS NULL THEN 'unitless' ELSE 'assumed_canonical' END;
    ELSIF canonical IS NULL THEN reason:='unexpected_unit';
    ELSIF requested_unit=canonical OR requested_unit=a.unit_label THEN unit_mode:='canonical_or_label';
    ELSE
      SELECT * INTO conversion FROM catalog_search.convert_unit(n,requested_unit,canonical);
      IF conversion.status NOT IN ('converted','unchanged') THEN reason:=conversion.status;
      ELSE n:=conversion.value_out;unit_mode:=conversion.status;rule_version:=conversion.rule_version;
        -- Explicit precision policy for finite approximate stored inverse factors.
        epsilon:=greatest(pg_catalog.abs(n)*0.000000000001,0.000000000001);
      END IF;
    END IF;
  END IF;
  IF reason IS NOT NULL THEN RETURN pg_catalog.jsonb_build_object('status','unapplied','path',path,'reason',reason,'candidates','[]'::jsonb); END IF;
  RETURN pg_catalog.jsonb_build_object('status','applied','path',path,'attribute_id',a.attribute_id,'data_type',a.data_type,
    'op',p_spec->>'op','numeric_value',n::text,
    'text_value',CASE WHEN a.data_type='text' THEN p_spec->>'value' END,
    'boolean_value',CASE WHEN a.data_type='boolean' THEN (p_spec->>'value')::boolean END,
    'unit_code',canonical,'unit_mode',unit_mode,'rule_version',rule_version,'comparison_epsilon',epsilon::text,
    'requested_value',p_spec->'value','requested_unit',requested_unit,
    'outside_documented_range',CASE WHEN n IS NULL THEN false ELSE
      coalesce((a.min_value IS NOT NULL AND n+epsilon<a.min_value) OR (a.max_value IS NOT NULL AND n-epsilon>a.max_value),false) END);
END $$;

CREATE FUNCTION catalog_search.resolve_typed_filters(p_category_ids text[],p_filters jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE cid text; term record; item record; candidates jsonb; diagnostics jsonb:='[]'; brands text[]:='{}';
  feature_ids text[]; brand_bindings jsonb:='[]'; feature_bindings jsonb; specs jsonb; unapplied jsonb; branches jsonb:='[]'; resolved jsonb; branch_status text;
  conflicts jsonb; global_ambiguity boolean:=false; conflict_count integer:=0; any_ambiguity boolean:=false;
BEGIN
  PERFORM catalog_search.validate_filter_input(p_filters);
  IF p_category_ids IS NULL OR pg_catalog.cardinality(p_category_ids)<1 OR pg_catalog.cardinality(p_category_ids)>1907
    OR EXISTS(SELECT 1 FROM pg_catalog.unnest(p_category_ids) x WHERE x IS NULL OR x !~ '^[A-Z]{2}[0-9]{4}$'
      OR NOT EXISTS(SELECT 1 FROM public.categories c WHERE c.id=x AND c.level=3)) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected validated leaf category scope';
  END IF;
  FOR term IN SELECT x.value#>>'{}' query,(x.n-1)::integer idx
    FROM pg_catalog.jsonb_array_elements(coalesce(p_filters->'brand_queries','[]')) WITH ORDINALITY x(value,n) LOOP
    SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('id',b.id,'label',b.label) ORDER BY b.id),'[]') INTO candidates
      FROM catalog_search.filter_name_candidates('brand',term.query) b;
    IF pg_catalog.jsonb_array_length(candidates)=1 THEN brands:=pg_catalog.array_append(brands,candidates->0->>'id');
      brand_bindings:=brand_bindings||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/brand_queries/'||term.idx,'brand_id',candidates->0->>'id'));
    ELSE global_ambiguity:=true;
      diagnostics:=diagnostics||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/brand_queries/'||term.idx,
        'reason',CASE WHEN pg_catalog.jsonb_array_length(candidates)=0 THEN 'unknown_brand' ELSE 'ambiguous_brand' END,'candidates',candidates));
    END IF;
  END LOOP;
  SELECT coalesce(pg_catalog.array_agg(DISTINCT x ORDER BY x),'{}') INTO brands FROM pg_catalog.unnest(brands) x;
  FOR cid IN SELECT DISTINCT x FROM pg_catalog.unnest(p_category_ids) x ORDER BY x LOOP
    specs:='[]';unapplied:=diagnostics;feature_ids:='{}';feature_bindings:='[]';conflicts:='[]';branch_status:='ready';
    FOR item IN SELECT x.value spec,(x.n-1)::integer idx
      FROM pg_catalog.jsonb_array_elements(coalesce(p_filters->'specs','[]')) WITH ORDINALITY x(value,n) LOOP
      resolved:=catalog_search.resolve_spec(cid,item.spec,item.idx);
      IF resolved->>'status'='applied' THEN specs:=specs||pg_catalog.jsonb_build_array(resolved);
      ELSE unapplied:=unapplied||pg_catalog.jsonb_build_array(resolved-'status'); END IF;
    END LOOP;
    FOR term IN SELECT x.value#>>'{}' query,(x.n-1)::integer idx
      FROM pg_catalog.jsonb_array_elements(coalesce(p_filters->'feature_queries_all','[]')) WITH ORDINALITY x(value,n) LOOP
      SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('id',f.id,'label',f.label) ORDER BY f.id),'[]') INTO candidates
        FROM catalog_search.filter_name_candidates('feature',term.query,cid) f;
      IF pg_catalog.jsonb_array_length(candidates)=1 THEN feature_ids:=pg_catalog.array_append(feature_ids,candidates->0->>'id');
        feature_bindings:=feature_bindings||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/feature_queries_all/'||term.idx,'feature_id',candidates->0->>'id'));
      ELSE unapplied:=unapplied||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/feature_queries_all/'||term.idx,
        'reason',CASE WHEN pg_catalog.jsonb_array_length(candidates)=0 THEN 'feature_not_in_category' ELSE 'ambiguous_feature' END,'candidates',candidates)); END IF;
    END LOOP;
    -- Numeric AND constraints form one interval per attribute; include conversion precision explicitly.
    SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(x) ORDER BY x.attribute_id),'[]') INTO conflicts FROM (
      SELECT s->>'attribute_id' attribute_id,pg_catalog.jsonb_agg(s->>'path' ORDER BY s->>'path') paths
      FROM pg_catalog.jsonb_array_elements(specs) s GROUP BY s->>'attribute_id'
      HAVING max(CASE WHEN s->>'op' IN ('eq','gte') AND s->>'data_type'='number' THEN (s->>'numeric_value')::numeric-(s->>'comparison_epsilon')::numeric END)
        > min(CASE WHEN s->>'op' IN ('eq','lte') AND s->>'data_type'='number' THEN (s->>'numeric_value')::numeric+(s->>'comparison_epsilon')::numeric END)
        OR pg_catalog.count(DISTINCT CASE WHEN s->>'data_type'='text' THEN s->>'text_value' END)>1
        OR pg_catalog.count(DISTINCT CASE WHEN s->>'data_type'='boolean' THEN s->>'boolean_value' END)>1
    )x;
    IF EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(unapplied) x WHERE x->>'reason' IN ('ambiguous_attribute','ambiguous_feature','unknown_brand','ambiguous_brand')) THEN
      branch_status:='needs_clarification';any_ambiguity:=true;
    ELSIF pg_catalog.jsonb_array_length(conflicts)>0 THEN branch_status:='constraint_conflict';conflict_count:=conflict_count+1;
    ELSIF pg_catalog.jsonb_array_length(unapplied)>0 THEN branch_status:='partial'; END IF;
    SELECT coalesce(pg_catalog.array_agg(DISTINCT x ORDER BY x),'{}') INTO feature_ids FROM pg_catalog.unnest(feature_ids) x;
    branches:=branches||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('category_id',cid,'status',branch_status,
      'brand_ids',brands,'resolved_brands',brand_bindings,'feature_ids_all',feature_ids,'resolved_features',feature_bindings,'resolved_specs',specs,'unapplied_filters',unapplied,
      'conflicts',conflicts,'all_requested_filters_applied',pg_catalog.jsonb_array_length(unapplied)=0,
      'price',pg_catalog.jsonb_build_object('min',(p_filters#>>'{price,min}'),'max',(p_filters#>>'{price,max}'),'currency','THB')));
  END LOOP;
  RETURN pg_catalog.jsonb_build_object('resolver_version','typed-filters-v1',
    'status',CASE WHEN global_ambiguity OR any_ambiguity THEN 'needs_clarification'
      WHEN conflict_count=pg_catalog.jsonb_array_length(branches) THEN 'constraint_conflict' ELSE 'resolved' END,
    'all_requested_filters_applied',NOT EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(branches) b WHERE b->>'all_requested_filters_applied'='false'),
    'branches',branches);
END $$;

REVOKE ALL ON FUNCTION catalog_search.filter_name_candidates(text,text,text),catalog_search.validate_filter_input(jsonb),
  catalog_search.resolve_spec(text,jsonb,integer),catalog_search.resolve_typed_filters(text[],jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog_search.filter_name_candidates(text,text,text),catalog_search.validate_filter_input(jsonb),
  catalog_search.resolve_spec(text,jsonb,integer),catalog_search.resolve_typed_filters(text[],jsonb) TO catalog_search_reader;

-- Down Migration
DROP FUNCTION catalog_search.resolve_typed_filters(text[],jsonb);
DROP FUNCTION catalog_search.resolve_spec(text,jsonb,integer);
DROP FUNCTION catalog_search.validate_filter_input(jsonb);
DROP FUNCTION catalog_search.filter_name_candidates(text,text,text);

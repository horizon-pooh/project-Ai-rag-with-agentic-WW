-- Up Migration
-- Additive helpers; applied 0001-0004 and legacy entry points remain unchanged.
CREATE FUNCTION catalog_search.validate_filter_input_v2(p_filters jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE legacy jsonb; rewritten jsonb:='[]'; s jsonb; k text; a jsonb;
BEGIN
  IF p_filters IS NULL OR pg_catalog.jsonb_typeof(p_filters)<>'object' THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected filters object';
  END IF;
  legacy:=p_filters-'category_ids'-'feature_ids_all';
  FOREACH k IN ARRAY ARRAY['category_ids','feature_ids_all'] LOOP
    IF p_filters ? k THEN
      a:=p_filters->k;
      IF pg_catalog.jsonb_typeof(a)<>'array' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected ID array'; END IF;
      IF pg_catalog.jsonb_array_length(a)<1 OR pg_catalog.jsonb_array_length(a)>20
        OR EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(a) x WHERE pg_catalog.jsonb_typeof(x)<>'string'
          OR (k='category_ids' AND x#>>'{}' !~ '^[A-Z]{2}[0-9]{4}$')
          OR (k='feature_ids_all' AND x#>>'{}' !~ '^FT[0-9]{4}$'))
        OR (SELECT pg_catalog.count(DISTINCT x) FROM pg_catalog.jsonb_array_elements(a) x)<>pg_catalog.jsonb_array_length(a) THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid or duplicate IDs';
      END IF;
    END IF;
  END LOOP;
  IF pg_catalog.jsonb_typeof(p_filters->'specs')='array' THEN
    FOR s IN SELECT * FROM pg_catalog.jsonb_array_elements(p_filters->'specs') LOOP
      IF s ? 'attribute_id' THEN
        IF pg_catalog.jsonb_typeof(s->'attribute_id')<>'string' OR s->>'attribute_id' !~ '^AT[0-9]{4}$' THEN
          RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid attribute ID';
        END IF;
        IF NOT(s ? 'attribute_query') THEN s:=s||pg_catalog.jsonb_build_object('attribute_query',s->>'attribute_id'); END IF;
      END IF;
      rewritten:=rewritten||pg_catalog.jsonb_build_array(s-'attribute_id');
    END LOOP;
    legacy:=legacy||pg_catalog.jsonb_build_object('specs',rewritten);
  END IF;
  PERFORM catalog_search.validate_filter_input(legacy);
END $$;

CREATE FUNCTION catalog_search.category_id_scope(p_ids text[]) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE issues jsonb; leaves jsonb; missing_scope jsonb;
BEGIN
  PERFORM catalog_search.validate_filter_input_v2(pg_catalog.jsonb_build_object('category_ids',p_ids));
  SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('path','/filters/category_ids/'||(x.n-1),
    'reason','unknown_category_id','candidates','[]'::jsonb) ORDER BY x.n),'[]') INTO issues
  FROM pg_catalog.unnest(p_ids) WITH ORDINALITY x(id,n)
  WHERE NOT EXISTS(SELECT 1 FROM public.categories c WHERE c.id=x.id);
  IF pg_catalog.jsonb_array_length(issues)>0 THEN
    RETURN pg_catalog.jsonb_build_object('status','needs_clarification','issues',issues,'category_ids','[]'::jsonb,'scopes','[]'::jsonb);
  END IF;
  SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) ORDER BY c.root_id,c.category_id),'[]') INTO leaves
    FROM catalog_search.category_leaf_scopes(p_ids) c;
  SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('path','/filters/category_ids/'||(x.n-1),
    'reason','empty_category_scope','candidates','[]'::jsonb) ORDER BY x.n),'[]') INTO missing_scope
  FROM pg_catalog.unnest(p_ids) WITH ORDINALITY x(id,n)
  WHERE NOT EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(leaves) l WHERE l->>'root_id'=x.id);
  IF pg_catalog.jsonb_array_length(missing_scope)>0 THEN
    RETURN pg_catalog.jsonb_build_object('status','needs_clarification','issues',missing_scope,'category_ids','[]'::jsonb,'scopes','[]'::jsonb);
  END IF;
  RETURN pg_catalog.jsonb_build_object('status','resolved','issues','[]'::jsonb,'scopes',leaves,
    'category_ids',(SELECT coalesce(pg_catalog.jsonb_agg(x.id ORDER BY x.id),'[]') FROM
      (SELECT DISTINCT l->>'category_id' id FROM pg_catalog.jsonb_array_elements(leaves) l)x));
END $$;

CREATE FUNCTION catalog_search.resolve_spec_v2(p_category_id text,p_spec jsonb,p_index integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE candidates jsonb; a record; n numeric; epsilon numeric:=0; conversion record;
  unit_mode text:='unitless'; requested_unit text:=pg_catalog.btrim(p_spec->>'unit'); reason text;
  path text:='/filters/specs/'||p_index; canonical text; rule_version text; selected record;
BEGIN
  PERFORM catalog_search.validate_filter_input_v2(pg_catalog.jsonb_build_object('specs',pg_catalog.jsonb_build_array(p_spec)));
  IF NOT(p_spec ? 'attribute_id') THEN RETURN catalog_search.resolve_spec(p_category_id,p_spec,p_index); END IF;
  IF p_index IS NULL OR p_index<0 OR p_index>19 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid spec position'; END IF;
  IF NOT EXISTS(SELECT 1 FROM public.attribute_definitions d WHERE d.id=p_spec->>'attribute_id') THEN
    RETURN pg_catalog.jsonb_build_object('status','unapplied','path',path||'/attribute_id','reason','unknown_attribute_id','candidates','[]'::jsonb);
  END IF;
  SELECT c.* INTO selected FROM public.category_attribute_search_catalog c
    WHERE c.category_id=p_category_id AND c.attribute_id=p_spec->>'attribute_id';
  IF NOT FOUND THEN RETURN pg_catalog.jsonb_build_object('status','unapplied','path',path||'/attribute_id',
    'reason','attribute_id_not_in_category','candidates','[]'::jsonb); END IF;
  IF p_spec ? 'attribute_query' THEN
    IF NOT EXISTS(
    SELECT 1 FROM catalog_search.attribute_label_matches(p_category_id,p_spec->>'attribute_query') c
    WHERE c.attribute_id=selected.attribute_id AND CASE
      WHEN catalog_search.normalize_label(p_spec->>'attribute_query') ~ '^at[0-9]{4}$'
      THEN pg_catalog.lower(c.attribute_id)=catalog_search.normalize_label(p_spec->>'attribute_query') ELSE true END) THEN
    RETURN pg_catalog.jsonb_build_object('status','unapplied','path',path||'/attribute_id',
      'reason','attribute_id_name_mismatch','candidates',pg_catalog.jsonb_build_array(
        pg_catalog.jsonb_build_object('id',selected.attribute_id,'label',selected.name_th)));
  END IF;
    END IF;
  a:=selected;
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

CREATE FUNCTION catalog_search.resolve_typed_filters_v2(p_category_ids text[],p_filters jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE cid text; term record; item record; candidates jsonb; diagnostics jsonb:='[]'; brands text[]:='{}';
  feature_ids text[]; brand_bindings jsonb:='[]'; feature_bindings jsonb; specs jsonb; unapplied jsonb; branches jsonb:='[]'; resolved jsonb; branch_status text;
  id_scope jsonb; id_scope_ids text[]; id_conflict boolean; id_clarify boolean;
  conflicts jsonb; global_ambiguity boolean:=false; conflict_count integer:=0; any_ambiguity boolean:=false;
BEGIN
  PERFORM catalog_search.validate_filter_input_v2(p_filters);
  IF p_category_ids IS NULL OR pg_catalog.cardinality(p_category_ids)<1 OR pg_catalog.cardinality(p_category_ids)>1907
    OR EXISTS(SELECT 1 FROM pg_catalog.unnest(p_category_ids) x WHERE x IS NULL OR x !~ '^[A-Z]{2}[0-9]{4}$'
      OR NOT EXISTS(SELECT 1 FROM public.categories c WHERE c.id=x AND c.level=3)) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Expected validated leaf category scope';
  END IF;
  IF p_filters ? 'category_ids' THEN
    id_scope:=catalog_search.category_id_scope(ARRAY(SELECT pg_catalog.jsonb_array_elements_text(p_filters->'category_ids')));
    IF id_scope->>'status'<>'resolved' THEN
      RETURN pg_catalog.jsonb_build_object('resolver_version','typed-filters-ids-v1','status',id_scope->>'status',
        'all_requested_filters_applied',false,'selector_issues',id_scope->'issues','branches','[]'::jsonb);
    END IF;
    SELECT pg_catalog.array_agg(x ORDER BY x) INTO id_scope_ids FROM pg_catalog.jsonb_array_elements_text(id_scope->'category_ids') x;
    IF id_scope_ids IS DISTINCT FROM ARRAY(SELECT DISTINCT x FROM pg_catalog.unnest(p_category_ids) x ORDER BY x) THEN
      RETURN pg_catalog.jsonb_build_object('resolver_version','typed-filters-ids-v1','status','constraint_conflict',
        'all_requested_filters_applied',false,'selector_issues',pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
          'path','/filters/category_ids','reason','category_scope_mismatch','candidates','[]'::jsonb)),'branches','[]'::jsonb);
    END IF;
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
    specs:='[]';unapplied:=diagnostics;feature_ids:='{}';feature_bindings:='[]';conflicts:='[]';branch_status:='ready';id_conflict:=false;id_clarify:=false;
    FOR item IN SELECT x.value spec,(x.n-1)::integer idx
      FROM pg_catalog.jsonb_array_elements(coalesce(p_filters->'specs','[]')) WITH ORDINALITY x(value,n) LOOP
      resolved:=catalog_search.resolve_spec_v2(cid,item.spec,item.idx);
      IF item.spec ? 'attribute_id' AND resolved->>'status'<>'applied' THEN
        IF resolved->>'reason' IN ('attribute_id_not_in_category','attribute_id_name_mismatch') THEN id_conflict:=true;
        ELSE id_clarify:=true; END IF;
      END IF;
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
    FOR term IN SELECT x.value#>>'{}' id,(x.n-1)::integer idx
      FROM pg_catalog.jsonb_array_elements(coalesce(p_filters->'feature_ids_all','[]')) WITH ORDINALITY x(value,n) LOOP
      IF NOT EXISTS(SELECT 1 FROM public.features f WHERE f.id=term.id) THEN
        id_clarify:=true;
        unapplied:=unapplied||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/feature_ids_all/'||term.idx,
          'reason','unknown_feature_id','candidates','[]'::jsonb));
      ELSIF NOT EXISTS(SELECT 1 FROM public.category_features cf WHERE cf.category_id=cid AND cf.feature_id=term.id) THEN
        id_conflict:=true;
        unapplied:=unapplied||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/feature_ids_all/'||term.idx,
          'reason','feature_id_not_in_category','candidates','[]'::jsonb));
      ELSE
        feature_ids:=pg_catalog.array_append(feature_ids,term.id);
        feature_bindings:=feature_bindings||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/feature_ids_all/'||term.idx,'feature_id',term.id));
      END IF;
    END LOOP;
    IF pg_catalog.jsonb_array_length(coalesce(p_filters->'feature_queries_all','[]'))>0
      AND pg_catalog.jsonb_array_length(coalesce(p_filters->'feature_ids_all','[]'))>0 AND NOT id_conflict AND NOT id_clarify THEN
      IF EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(unapplied) u WHERE u->>'path' LIKE '/filters/feature_queries_all/%') THEN
        id_clarify:=true;
        unapplied:=unapplied||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('path','/filters/feature_ids_all',
          'reason','feature_name_unresolved','candidates','[]'::jsonb));

      END IF;
    END IF;
    -- Numeric AND constraints form one interval per attribute; include conversion precision explicitly.
    SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(x) ORDER BY x.attribute_id),'[]') INTO conflicts FROM (
      SELECT s->>'attribute_id' attribute_id,pg_catalog.jsonb_agg(s->>'path' ORDER BY s->>'path') paths
      FROM pg_catalog.jsonb_array_elements(specs) s GROUP BY s->>'attribute_id'
      HAVING max(CASE WHEN s->>'op' IN ('eq','gte') AND s->>'data_type'='number' THEN (s->>'numeric_value')::numeric-(s->>'comparison_epsilon')::numeric END)
        > min(CASE WHEN s->>'op' IN ('eq','lte') AND s->>'data_type'='number' THEN (s->>'numeric_value')::numeric+(s->>'comparison_epsilon')::numeric END)
        OR pg_catalog.count(DISTINCT CASE WHEN s->>'data_type'='text' THEN s->>'text_value' END)>1
        OR pg_catalog.count(DISTINCT CASE WHEN s->>'data_type'='boolean' THEN s->>'boolean_value' END)>1
    )x;
    IF id_clarify OR EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(unapplied) x WHERE x->>'reason' IN ('ambiguous_attribute','ambiguous_feature','unknown_brand','ambiguous_brand')) THEN
      branch_status:='needs_clarification';any_ambiguity:=true;
    ELSIF id_conflict OR pg_catalog.jsonb_array_length(conflicts)>0 THEN branch_status:='constraint_conflict';conflict_count:=conflict_count+1;
    ELSIF pg_catalog.jsonb_array_length(unapplied)>0 THEN branch_status:='partial'; END IF;
    SELECT coalesce(pg_catalog.array_agg(DISTINCT x ORDER BY x),'{}') INTO feature_ids FROM pg_catalog.unnest(feature_ids) x;
    branches:=branches||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('category_id',cid,'status',branch_status,
      'brand_ids',brands,'resolved_brands',brand_bindings,'feature_ids_all',feature_ids,'resolved_features',feature_bindings,'resolved_specs',specs,'unapplied_filters',unapplied,
      'conflicts',conflicts,'all_requested_filters_applied',pg_catalog.jsonb_array_length(unapplied)=0,
      'price',pg_catalog.jsonb_build_object('min',(p_filters#>>'{price,min}'),'max',(p_filters#>>'{price,max}'),'currency','THB')));
  END LOOP;
  RETURN pg_catalog.jsonb_build_object('resolver_version','typed-filters-ids-v1',
    'status',CASE WHEN global_ambiguity OR any_ambiguity THEN 'needs_clarification'
      WHEN conflict_count=pg_catalog.jsonb_array_length(branches) THEN 'constraint_conflict' ELSE 'resolved' END,
    'all_requested_filters_applied',NOT EXISTS(SELECT 1 FROM pg_catalog.jsonb_array_elements(branches) b WHERE b->>'all_requested_filters_applied'='false'),
    'branches',branches);
END $$;

REVOKE ALL ON FUNCTION catalog_search.validate_filter_input_v2(jsonb),catalog_search.category_id_scope(text[]),
 catalog_search.resolve_spec_v2(text,jsonb,integer),catalog_search.resolve_typed_filters_v2(text[],jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog_search.validate_filter_input_v2(jsonb),catalog_search.category_id_scope(text[]),
 catalog_search.resolve_spec_v2(text,jsonb,integer),catalog_search.resolve_typed_filters_v2(text[],jsonb) TO catalog_search_reader;

-- Down Migration
DROP FUNCTION catalog_search.resolve_typed_filters_v2(text[],jsonb);
DROP FUNCTION catalog_search.resolve_spec_v2(text,jsonb,integer);
DROP FUNCTION catalog_search.category_id_scope(text[]);
DROP FUNCTION catalog_search.validate_filter_input_v2(jsonb);

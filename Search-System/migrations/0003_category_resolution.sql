-- Up Migration
-- Reuse pg_trgm already installed in extensions; never install/move/drop an extension here.
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
    WHERE e.extname='pg_trgm' AND n.nspname='extensions') THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Expected existing pg_trgm in extensions; inspect baseline before applying';
  END IF;
END $$;

-- Read-only lexical evidence. Similarity is a score, NOT a confidence/probability.
CREATE FUNCTION catalog_search.category_candidates(p_query text,p_limit integer DEFAULT 8)
RETURNS TABLE(category_id text,parent_id text,level integer,name_th text,name_en text,
  matched_label text,match_source text,match_kind text,score real,total_candidates integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE q text:=catalog_search.normalize_label(p_query);
BEGIN
  IF q IS NULL OR q='' OR pg_catalog.char_length(p_query)>200 OR p_limit IS NULL OR p_limit<1 OR p_limit>10 THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid category candidate request';
  END IF;
  -- An explicit category ID is an identity lock, including an unknown ID.
  IF q ~ '^[a-z]{2}[0-9]{4}$' THEN
    RETURN QUERY SELECT c.category_id,c.parent_id,c.level::integer,c.name_th,c.name_en,
      c.category_id,'id'::text,'exact'::text,1::real,1
    FROM public.category_search_catalog c WHERE pg_catalog.lower(c.category_id)=q;
    RETURN;
  END IF;
  RETURN QUERY
  WITH labels AS MATERIALIZED (
    SELECT c.category_id,c.parent_id,c.level::integer,c.name_th,c.name_en,l.label,l.source,l.priority,
      catalog_search.normalize_label(l.label) norm
    FROM public.category_search_catalog c CROSS JOIN LATERAL (
      SELECT c.category_id AS label,'id'::text AS source,0 AS priority
      UNION ALL SELECT c.name_th,'name',1
      UNION ALL SELECT c.name_en,'name',1
      UNION ALL SELECT a,'alias',2 FROM pg_catalog.unnest(c.aliases) a
    )l WHERE l.label IS NOT NULL AND pg_catalog.btrim(l.label)<>''
  ), scored AS (
    SELECT l.*,CASE WHEN l.norm=q THEN 1::real ELSE extensions.similarity(q,l.norm) END AS similarity,
      l.norm=q AS exact FROM labels l
  ), eligible AS (
    SELECT s.* FROM scored s WHERE s.exact OR (
      NOT EXISTS(SELECT 1 FROM scored x WHERE x.exact)
      AND q !~ '^[a-z]{2}[0-9]{4}$' AND pg_catalog.char_length(q)>=3
      AND s.source<>'id' AND s.similarity>=0.35::real)
  ), deduplicated AS (
    SELECT DISTINCT ON(e.category_id) e.* FROM eligible e
    ORDER BY e.category_id,e.exact DESC,e.similarity DESC,e.priority,e.label COLLATE "C"
  )
  SELECT d.category_id,d.parent_id,d.level,d.name_th,d.name_en,d.label,d.source,
    CASE WHEN d.exact THEN 'exact' ELSE 'similar' END,d.similarity,pg_catalog.count(*) OVER()::integer
  FROM deduplicated d ORDER BY d.exact DESC,d.similarity DESC,d.category_id LIMIT p_limit;
END $$;

-- Expand through actual parent IDs and levels, never code-prefix or fuzzy inference.
-- Bound root input, hierarchy depth and return only level-3 categories. No product scan.
CREATE FUNCTION catalog_search.category_leaf_scopes(p_root_ids text[])
RETURNS TABLE(root_id text,category_id text,name_th text,path_ids text[],path_labels text[])
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path='' AS $$
BEGIN
  IF p_root_ids IS NULL OR pg_catalog.cardinality(p_root_ids)<1 OR pg_catalog.cardinality(p_root_ids)>40
    OR EXISTS(SELECT 1 FROM pg_catalog.unnest(p_root_ids) x WHERE x IS NULL OR x !~ '^[A-Z]{2}[0-9]{4}$') THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Invalid category roots';
  END IF;
  IF EXISTS(SELECT 1 FROM pg_catalog.unnest(p_root_ids) x
    WHERE NOT EXISTS(SELECT 1 FROM public.categories c WHERE c.id=x)) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='Unknown category root';
  END IF;
  -- Refuse damaged hierarchy rather than silently omit descendants or follow cycles.
  IF EXISTS(SELECT 1 FROM public.categories c LEFT JOIN public.categories p ON p.id=c.parent_id
    WHERE c.level NOT IN (1,2,3) OR c.level IS NULL
      OR (c.level=1 AND c.parent_id IS NOT NULL)
      OR (c.level>1 AND (p.id IS NULL OR p.level<>c.level-1))) THEN
    RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='Category hierarchy needs repair';
  END IF;
  RETURN QUERY
  WITH RECURSIVE tree AS (
    SELECT c.id AS root_id,c.id,c.level FROM public.categories c WHERE c.id=ANY(p_root_ids)
    UNION ALL
    SELECT t.root_id,c.id,c.level FROM tree t JOIN public.categories c ON c.parent_id=t.id
    WHERE c.level=t.level+1 AND t.level<3
  )
  SELECT t.root_id,c.id,c.name_th,ARRAY[g.id,p.id,c.id],ARRAY[g.name_th,p.name_th,c.name_th]
  FROM tree t JOIN public.categories c ON c.id=t.id
    JOIN public.categories p ON p.id=c.parent_id JOIN public.categories g ON g.id=p.parent_id
  WHERE t.level=3 ORDER BY t.root_id,c.id;
END $$;

REVOKE ALL ON FUNCTION catalog_search.category_candidates(text,integer),catalog_search.category_leaf_scopes(text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog_search.category_candidates(text,integer),catalog_search.category_leaf_scopes(text[]) TO catalog_search_reader;

-- Down Migration
-- Keep shared metadata, Commit 11 helpers and the pre-existing extension.
DROP FUNCTION catalog_search.category_leaf_scopes(text[]);
DROP FUNCTION catalog_search.category_candidates(text,integer);

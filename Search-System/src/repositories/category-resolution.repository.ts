import type { PoolClient } from "pg";
import type { CategoryCandidate, CategoryCandidateGroup, CategoryLeafScope,
  CategoryResolutionRepository } from "../types/category-resolution.types.js";

export function createCategoryResolutionRepository(client: Pick<PoolClient,"query">): CategoryResolutionRepository {
  return {
    async findCandidates(phrases) {
      if (!Array.isArray(phrases) || phrases.length<1 || phrases.length>5 || phrases.some(p=>
        typeof p!=="string" || !p.trim() || [...p].length>200)) throw new TypeError("Invalid category phrases");
      const rows=(await client.query<CategoryCandidate & {phrase_index: number}>(`
        SELECT (q.n-1)::integer AS phrase_index,c.*
        FROM unnest($1::text[]) WITH ORDINALITY q(phrase,n)
        CROSS JOIN LATERAL catalog_search.category_candidates(q.phrase,8) c
        ORDER BY q.n,c.score DESC,c.category_id`,[phrases])).rows;
      return phrases.map((_,phrase_index):CategoryCandidateGroup=>({phrase_index,
        candidates:rows.filter(r=>r.phrase_index===phrase_index).map(({phrase_index: _index,...candidate})=>candidate)}));
    },
    async expandRoots(rootIds) {
      if (!Array.isArray(rootIds) || rootIds.length>40 || rootIds.some(id=>typeof id!=="string" || !/^[A-Z]{2}[0-9]{4}$/.test(id)))
        throw new TypeError("Invalid category root IDs");
      if (!rootIds.length) return [];
      return (await client.query<CategoryLeafScope>(
        "SELECT * FROM catalog_search.category_leaf_scopes($1::text[])",[rootIds])).rows;
    },
  };
}

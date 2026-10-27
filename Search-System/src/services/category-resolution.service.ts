import type { CategoryGroupResolution, CategoryResolution, CategoryResolutionRepository } from "../types/category-resolution.types.js";

/** A literal category label or explicit OR list, not an arbitrary conversation parser. */
export function splitCategoryQuery(query: string): string[] {
  if (typeof query!=="string" || !query.trim() || [...query].length>200)
    throw new TypeError("Category query must contain 1–200 characters");
  const parts=query.trim().split(/\s*\|\s*|\s*หรือ\s*|\s+or\s+/iu).map(p=>p.trim());
  if (parts.some(p=>!p) || parts.length>5) throw new TypeError("Expected 1–5 nonempty category phrases");
  return [...new Set(parts)];
}

export async function resolveCategoryQuery(repository: CategoryResolutionRepository,query: string): Promise<CategoryResolution> {
  const phrases=splitCategoryQuery(query);
  const found=await repository.findCandidates(phrases);
  const byIndex=new Map(found.map(g=>[g.phrase_index,g.candidates]));
  const exactRoots=[...new Set(found.flatMap(g=>g.candidates.filter(c=>
    c.match_kind==="exact" && c.total_candidates<=8).map(c=>c.category_id)))];
  const expansions=await repository.expandRoots(exactRoots);
  const scopeKeys=new Map(exactRoots.map(id=>[id,[...new Set(expansions.filter(s=>s.root_id===id)
    .map(s=>s.category_id))].sort().join(",")]));
  const groups=phrases.map((phrase,index):CategoryGroupResolution=>{
    const candidates=byIndex.get(index) ?? [];
    const base={phrase,candidates,selected_root_id:null,equivalent_root_ids:[],candidate_total:candidates[0]?.total_candidates ?? 0};
    if (!candidates.length) return {...base,status:"needs_clarification",reason:"unknown_category"};
    if (candidates[0]!.total_candidates>8) return {...base,status:"needs_clarification",reason:"candidate_limit_exceeded"};
    if (candidates.some(c=>c.match_kind!=="exact")) return {...base,status:"needs_clarification",reason:"similar_labels_require_confirmation"};
    if (candidates.some(c=>!scopeKeys.get(c.category_id))) return {...base,status:"needs_clarification",reason:"empty_category_scope"};
    const keys=new Set(candidates.map(c=>scopeKeys.get(c.category_id)));
    if (keys.size!==1) return {...base,status:"needs_clarification",reason:"ambiguous_exact_label"};
    // Different labels with identical leaf scope are safe to coalesce. Preserve evidence.
    const ordered=[...candidates].sort((a,b)=>b.level-a.level || a.category_id.localeCompare(b.category_id,"en"));
    return {...base,status:"resolved",reason:candidates.length>1?"equivalent_scopes":"exact_label",
      selected_root_id:ordered[0]!.category_id,equivalent_root_ids:ordered.map(c=>c.category_id).sort()};
  });
  const resolved=groups.every(g=>g.status==="resolved");
  const roots=new Set(groups.map(g=>g.selected_root_id));
  const scopes=resolved?expansions.filter(s=>roots.has(s.root_id)):[];
  return {status:resolved?"resolved":"needs_clarification",resolver_version:"category-lexical-v1",
    source_path:"/filters/category_query",query,groups,
    category_ids:[...new Set(scopes.map(s=>s.category_id))].sort(),scopes};
}

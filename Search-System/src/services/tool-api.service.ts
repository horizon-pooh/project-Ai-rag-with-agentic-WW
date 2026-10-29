import type {CatalogProductDetails,CatalogSearchSnapshot} from "../types/catalog-read.types.js";
import type {ToolSearchResponse} from "../types/tool-search.types.js";
import {parseToolSearchRequest} from "../validation/tool-search.validation.js";
import {mapCatalogSearchResponse} from "../validation/catalog-response.mapper.js";
export interface CatalogGateway {
  search(input:unknown):Promise<CatalogSearchSnapshot>;
  findDetails(article:number):Promise<CatalogProductDetails|null>;
}
/** Keep category inference explicit and bounded. No semantic/query rewriting or full-catalog scan. */
export function createToolApiService(catalog:CatalogGateway) {
  return {
    async search(input:unknown,requestId:string):Promise<ToolSearchResponse> {
      const parsed=parseToolSearchRequest(input);
      if(!parsed.valid)return {version:"1",request_id:requestId,status:"invalid_request",issues:parsed.issues};
      const original=parsed.value;
      const infer=!original.filters.category_query && !original.filters.category_ids &&
        !original.exact && Boolean(original.query.trim()) && !/^1[0-9]{5}$/.test(original.query.trim()) &&
        !/^[A-Z]{2}[0-9]{4}-[0-9]{3,}$/.test(original.query.trim());
      const request=infer ? {...original,filters:{...original.filters,category_query:original.query.trim()}} : original;
      const response=mapCatalogSearchResponse(await catalog.search(request),requestId);
      // The inferred scope was supplied by /query, not by a nonexistent input filter.
      if(infer) {
        if(response.status==="ok" || response.status==="no_matches") {
          response.applied_filters=response.applied_filters.map(f=>f.path==="/filters/category_query"?{...f,path:"/query"}:f);
          response.unapplied_filters=response.unapplied_filters.map(f=>f.path==="/filters/category_query"?{...f,path:"/query"}:f);
          response.warnings=[...response.warnings,{code:"category_inferred_from_query",message:response.warnings.some(w=>w.code==="category_semantic_scope_selected")?"ใช้คำค้นเลือกหมวดด้วยเวกเตอร์ ตรวจ semantic_available เพื่อดูวิธีจัดอันดับสินค้า":"The query was used as a literal category label; no semantic category inference was performed."}];
        } else if(response.status==="needs_clarification")response.clarifications=response.clarifications.map(i=>i.path==="/filters/category_query"?{...i,path:"/query"}:i);
      }
      return response;
    },
    details(article:number) { return catalog.findDetails(article); },
  };
}

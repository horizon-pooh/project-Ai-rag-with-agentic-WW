import {CatalogDataError,type CatalogSearchSnapshot} from "../types/catalog-read.types.js";
import type {AppliedFilter,UnappliedFilter,ToolSearchResponse,ProductFilterDiagnostic} from "../types/tool-search.types.js";
import type {KeywordDifference} from "../types/keyword-search.types.js";
import type {FilterDiagnostic} from "../types/typed-filter.types.js";
import {validateToolSearchResponse} from "./tool-search.validation.js";
import {decimalFact} from "../repositories/product-facts.repository.js";

function difference(d:KeywordDifference):ProductFilterDiagnostic {
  // v1 has no unit field for product differences. Explicitly label canonical units in reason.
  const unit=d.canonical_unit ? `; values in ${d.canonical_unit}` : "";
  const input=d.input_unit ? `; input ${String(d.input_value)} ${d.input_unit}` : "";
  return {path:d.path,requested_value:d.requested_value,actual_value:d.actual_value,
    reason:d.reason+unit+input};
}
function checked(response:ToolSearchResponse):ToolSearchResponse {
  if(!validateToolSearchResponse(response).valid)throw new CatalogDataError();
  return response;
}
/** Pure adapter to the existing v1 contract. HTTP/auth/error transport belong to Commit 17. */
export function mapCatalogSearchResponse(snapshot:CatalogSearchSnapshot,requestId:string):ToolSearchResponse {
  const base={version:"1" as const,request_id:requestId};
  const category=snapshot.category_resolution;
  if(category.status==="constraint_conflict")return checked({...base,status:"constraint_conflict",issues:
    (category.selector_issues?.length?category.selector_issues:[{path:category.source_path,reason:"category_scope_conflict"}])
    .map(i=>({path:i.path,code:i.reason,message:"Category selectors conflict"}))});
  if(category.status==="needs_clarification") {
    const issues:FilterDiagnostic[]=[...(category.selector_issues??[])];
    for(const group of category.groups.filter(g=>g.status!=="resolved"))issues.push({path:"/filters/category_query",reason:group.reason,
      candidates:group.candidates.map(c=>({id:c.category_id,label:c.name_th}))});
    if(!issues.length)issues.push({path:category.source_path,reason:"category_scope_required",candidates:[]});
    return checked({...base,status:"needs_clarification",clarifications:issues});
  }
  const search=snapshot.search;if(!search)throw new CatalogDataError();
  const resolution=search.filter_resolution;
  if(search.status==="needs_clarification") {
    const issues:FilterDiagnostic[]=[...(resolution?.selector_issues??[]),...(search.issues??[]).map(i=>({...i,candidates:[]}))];
    for(const branch of resolution?.branches??[])issues.push(...branch.unapplied_filters);
    if(!issues.length)issues.push({path:"/filters",reason:"typed_filter_clarification_required",candidates:[]});
    return checked({...base,status:"needs_clarification",clarifications:issues});
  }
  if(search.status==="constraint_conflict") {
    const issues=(search.issues??[]).map(i=>({path:i.path,code:i.reason,message:"Requested constraints conflict"}));
    for(const branch of resolution?.branches??[]) {
      for(const issue of branch.unapplied_filters)issues.push({path:issue.path,code:issue.reason,message:"Requested constraints conflict in "+branch.category_id});
      for(const conflict of branch.conflicts)for(const path of conflict.paths)issues.push({path,code:"constraint_conflict",message:"Conflicting constraints for "+conflict.attribute_id});
    }
    if(!issues.length)issues.push({path:"/filters",code:"constraint_conflict",message:"Requested constraints conflict"});
    return checked({...base,status:"constraint_conflict",issues});
  }
  if(!resolution || !search.meta || snapshot.products.length!==search.results.length)throw new CatalogDataError();
  const applied_filters:AppliedFilter[]=[],unapplied_filters:UnappliedFilter[]=[];
  const request=snapshot.request;
  if(request.exact?.article_id!==undefined)applied_filters.push({category_id:null,path:"/exact/article_id",resolved_id:null,op:"eq",value:request.exact.article_id,unit:null});
  if(request.exact?.model!==undefined)applied_filters.push({category_id:null,path:"/exact/model",resolved_id:null,op:"eq",value:request.exact.model,unit:null});
  for(const branch of resolution.branches) {
    for(const i of branch.unapplied_filters)unapplied_filters.push({category_id:branch.category_id,path:i.path,reason:i.reason});
    for(const c of branch.conflicts)for(const path of c.paths)unapplied_filters.push({category_id:branch.category_id,path,reason:"constraint_conflict"});
    if(branch.status!=="ready" && branch.status!=="partial")continue;
    if(request.filters.category_query)applied_filters.push({category_id:branch.category_id,path:"/filters/category_query",resolved_id:branch.category_id,op:"eq",value:request.filters.category_query,unit:null});
    if(request.filters.category_ids)applied_filters.push({category_id:branch.category_id,path:"/filters/category_ids",resolved_id:branch.category_id,op:"in",value:request.filters.category_ids,unit:null});
    for(const b of branch.resolved_brands)applied_filters.push({category_id:branch.category_id,path:b.path,resolved_id:b.brand_id,op:"eq",value:b.brand_id,unit:null});
    for(const f of branch.resolved_features)applied_filters.push({category_id:branch.category_id,path:f.path,resolved_id:f.feature_id,op:"eq",value:f.feature_id,unit:null});
    for(const s of branch.resolved_specs) {
      const value=s.data_type==="number"?decimalFact(s.numeric_value):s.data_type==="boolean"?s.boolean_value:s.text_value;
      if(value===null)throw new CatalogDataError();
      applied_filters.push({category_id:branch.category_id,path:s.path,resolved_id:s.attribute_id,op:s.op,value,unit:s.unit_code});
    }
    for(const bound of ["min","max"] as const)if(branch.price[bound]!==null) {
      const value=decimalFact(branch.price[bound]);if(value===null)throw new CatalogDataError();
      applied_filters.push({category_id:branch.category_id,path:"/filters/price/"+bound,resolved_id:null,
        op:bound==="min"?"gte":"lte",value,unit:"THB"});
    }
  }
  for(const i of resolution.selector_issues??[])unapplied_filters.push({category_id:null,path:i.path,reason:i.reason});
  const unique=[...new Map(unapplied_filters.map(i=>[JSON.stringify(i),i])).values()];
  if(search.meta.all_requested_filters_applied!==(unique.length===0))throw new CatalogDataError();
  return checked({...base,status:search.status,query:request.query,semantic_available:false,
    resolved_categories:search.resolved_categories??[],applied_filters,unapplied_filters:unique,warnings:[...search.warnings,...(category.semantic_warnings??[])],
    meta:{...search.meta},results:search.results.map((hit,index)=>{
      const product=snapshot.products[index]!;if(product.article_id!==hit.article_id)throw new CatalogDataError();
      return {product,match_type:hit.match_type,unmet_filters:hit.unmet_filters.map(difference),unknown_filters:hit.unknown_filters.map(difference)};
    })});
}

import type {PoolClient} from "pg";
import type {CategoryResolution,CategoryLeafScope,SelectorIssue} from "../types/category-resolution.types.js";
import type {ToolSearchFilters} from "../types/tool-search.types.js";
import {parseToolSearchRequest} from "../validation/tool-search.validation.js";
import {createCategoryResolutionRepository} from "../repositories/category-resolution.repository.js";
import {resolveCategoryQuery} from "./category-resolution.service.js";

interface IdScope {status:"resolved"|"needs_clarification";issues:SelectorIssue[];category_ids:string[];scopes:CategoryLeafScope[]}
/** Use a transaction-bound client for a consistent category/filter/search snapshot. */
export async function resolveCategoryFilters(client:Pick<PoolClient,"query">,filters:ToolSearchFilters):Promise<CategoryResolution>{
 const parsed=parseToolSearchRequest({query:"metadata resolution",filters});
 if(!parsed.valid)throw new TypeError("Invalid selector filters");
 const input=parsed.value.filters;
 const base:CategoryResolution={status:"needs_clarification",resolver_version:"category-selectors-v1",
   source_path:input.category_ids?"/filters/category_ids":"/filters/category_query",query:input.category_query??"",
   groups:[],category_ids:[],scopes:[],selector_issues:[]};
 if(!input.category_ids){
   if(input.category_query)return resolveCategoryQuery(createCategoryResolutionRepository(client),input.category_query);
   return {...base,selector_issues:[{path:"/filters/category_query",reason:"category_scope_required",candidates:[]}]};
 }
 const result=await client.query<{scope:IdScope}>("SELECT catalog_search.category_id_scope($1::text[]) AS scope",[input.category_ids]);
 const scope=result.rows[0]?.scope;if(!scope)throw new Error("Category ID scope unavailable");
 const verified={...base,input_category_ids:[...input.category_ids]};
 if(scope.status!=="resolved")return {...verified,selector_issues:scope.issues};
 if(input.category_query){
   const named=await resolveCategoryQuery(createCategoryResolutionRepository(client),input.category_query);
   if(named.status!=="resolved")return {...verified,groups:named.groups,selector_issues:[{
     path:"/filters/category_query",reason:"category_name_unresolved",candidates:[]}]};
   const allowed=new Set(named.category_ids);
   if(scope.category_ids.some(id=>!allowed.has(id)))return {...verified,status:"constraint_conflict",groups:named.groups,
     selector_issues:[{path:"/filters/category_ids",reason:"category_id_name_mismatch",candidates:[]}]};
   verified.groups=named.groups;
 }
 return {...verified,status:"resolved",category_ids:scope.category_ids,scopes:scope.scopes};
}

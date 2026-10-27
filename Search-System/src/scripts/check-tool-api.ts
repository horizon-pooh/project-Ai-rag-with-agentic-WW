import "dotenv/config";
import assert from "node:assert/strict";
import {readToolApiConfig} from "../config/tool-api.config.js";
import {validateToolSearchResponse} from "../validation/tool-search.validation.js";
import {validDetailsResponse} from "../schemas/tool-http.schema.js";

try {
 const config=readToolApiConfig(process.env);if(!config)throw new Error("API disabled");
 const base=new URL(process.env.SEARCH_API_BASE_URL??"http://127.0.0.1:3000");
 if(base.protocol!=="http:" || !["127.0.0.1","localhost","[::1]"].includes(base.hostname) || base.pathname!=="/" || base.search || base.hash || base.username || base.password)
  throw new Error("Use loopback URL for the local smoke check");
 const call=async(path:string,body?:unknown,authorized=true)=>{
  const response=await fetch(new URL(path,base),{method:body===undefined?"GET":"POST",redirect:"error",signal:AbortSignal.timeout(30000),
   headers:{...(authorized?{authorization:"Bearer "+config.token}:{}),...(body!==undefined?{"content-type":"application/json"}:{})},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
  const json=await response.json() as Record<string,any>;
  if(path!=="/health")assert.equal(response.headers.get("x-request-id"),json.request_id);
  return {response,json};
 };
 assert.equal((await call("/health",undefined,false)).response.status,200);
 assert.equal((await call("/api/search",{query:"ทีวี"},false)).response.status,401);
 assert.equal((await call("/api/search",{query:"ทีวี",limit:"5"})).response.status,400);
 const {response,json}=await call("/api/search",{query:"ทีวี",semantic_query:"ทีวีดูหนัง",filters:{category_query:"ทีวี OLED",category_ids:["TV0108"],
  feature_ids_all:["FT0023"],price:{max:25000},specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}]},limit:5});
 assert.equal(response.status,200);assert.equal(validateToolSearchResponse(json).valid,true);assert.equal(json.status,"ok");assert.equal(json.semantic_available,false);
 assert.ok(json.results.length>0);const article=json.results[0].product.article_id;
 const details=await call("/api/products/"+article);assert.equal(details.response.status,200);assert.equal(validDetailsResponse(details.json),true);
 assert.equal((await call("/api/products/106045")).response.status,404);
 assert.equal((await call("/api/products/199999")).response.status,404);
 const inferred=await call("/api/search",{query:"ทีวี",limit:1});assert.equal(inferred.json.status,"ok");
 assert.ok(inferred.json.warnings.some((w:{code:string})=>w.code==="category_inferred_from_query"));
 const semantic=await call("/api/search",{semantic_query:"ดูหนัง"});assert.equal(semantic.json.status,"needs_clarification");
 console.log(JSON.stringify({status:"tool_api_ready",search_meta:json.meta,details_article:article,
  details_sections:details.json.details.content_sections.length,unauthorized_status:401,invalid_request_status:400,draft_status:404,
  query_category_fallback:true,semantic_available:false,secret_printed:false},null,2));
} catch {
 console.error(JSON.stringify({status:"failed",message:"Check the running local API, private caller token, tunnel, migration0006 and reader access. No credentials or raw errors are printed."}));process.exitCode=1;
}

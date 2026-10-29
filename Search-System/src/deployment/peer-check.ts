import {validateToolSearchResponse} from "../validation/tool-search.validation.js";
import {validDetailsResponse} from "../schemas/tool-http.schema.js";
import type {ToolSearchResponse,SearchResultsResponse} from "../types/tool-search.types.js";
export interface PeerCall { (path:string,body?:unknown,authorized?:boolean):Promise<{http_status:number;body:unknown}> }
function ensure(value:unknown):asserts value {if(!value)throw new Error("Peer integration check failed");}
export async function checkPeerIntegration(call:PeerCall,draftArticle=106045) {
 if(!Number.isInteger(draftArticle)||draftArticle<100000||draftArticle>199999)throw new Error("Invalid draft Article");
 const checks:Array<{name:string;http_status:number}>=[];
 const health=await call("/health",undefined,false);ensure(health.http_status===200&&health.body&&typeof health.body==="object"&&"status" in health.body&&health.body.status==="ok");checks.push({name:"health",http_status:200});
 const unauthorized=await call("/api/search",{query:"ทีวี"},false);ensure(unauthorized.http_status===401);checks.push({name:"unauthorized",http_status:401});
 const invalid=await call("/api/search",{query:"ทีวี",limit:"5"});ensure(invalid.http_status===400&&validateToolSearchResponse(invalid.body).valid&&(invalid.body as ToolSearchResponse).status==="invalid_request");checks.push({name:"strict_json",http_status:400});
 const search=await call("/api/search",{version:"1",query:"เครื่องซักผ้า",semantic_query:"ซักเสื้อผ้าประจำวัน",filters:{category_ids:["AP0101"]},limit:5});
 ensure(search.http_status===200&&validateToolSearchResponse(search.body).valid);const s=search.body as SearchResultsResponse;ensure(s.status==="ok"&&s.results.length>0&&s.results.every(r=>r.product.category_id==="AP0101"));checks.push({name:"published_search",http_status:200});
 const article=s.results[0]!.product.article_id,details=await call("/api/products/"+article);ensure(details.http_status===200&&validDetailsResponse(details.body)&&details.body.details.product.article_id===article);checks.push({name:"details",http_status:200});
 const draft=await call("/api/products/"+draftArticle);ensure(draft.http_status===404&&draft.body&&typeof draft.body==="object"&&"status" in draft.body&&draft.body.status==="not_found");checks.push({name:"draft_hidden",http_status:404});
 const exact=await call("/api/search",{version:"1",filters:{category_ids:["AP0101"]},exact:{article_id:article},limit:5});ensure(exact.http_status===200&&validateToolSearchResponse(exact.body).valid);const e=exact.body as SearchResultsResponse;ensure(e.status==="ok"&&e.results.length===1&&e.results[0]!.product.article_id===article);checks.push({name:"identity_lock",http_status:200});
 const clarify=await call("/api/search",{semantic_query:"อยากดูหนัง"});ensure(clarify.http_status===200&&validateToolSearchResponse(clarify.body).valid&&(clarify.body as ToolSearchResponse).status==="needs_clarification");checks.push({name:"clarification",http_status:200});
 return {status:"peer_integration_ready",checks,article_id:article,semantic_available:s.semantic_available,warning_codes:s.warnings.map(w=>w.code),database_credentials_required:false};
}

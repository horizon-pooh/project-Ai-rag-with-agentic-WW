import {createHash,timingSafeEqual} from "node:crypto";
import type {FastifyInstance,FastifyError} from "fastify";
import type {ToolApiConfig} from "../config/tool-api.config.js";
import {createToolApiService,type CatalogGateway} from "../services/tool-api.service.js";
import {CatalogInputError,CatalogDataError} from "../types/catalog-read.types.js";
import type {ContractIssue} from "../types/tool-search.types.js";
import {searchRequestSchema} from "../schemas/search-request.schema.js";
import {searchResponseSchema} from "../schemas/search-response.schema.js";
import {detailsResponseSchema,httpErrorSchema,validDetailsResponse} from "../schemas/tool-http.schema.js";
import {validateToolSearchResponse} from "../validation/tool-search.validation.js";
import {databaseErrorCode} from "../database/postgres.js";

const emptyQuery={type:"object",additionalProperties:false,properties:{}};
function validationIssues(error:Partial<FastifyError>):ContractIssue[] {
 return (error.validation??[]).map(v=>{
  const property=v.params?.additionalProperty??v.params?.missingProperty;
  const suffix=typeof property==="string"?"/"+property.replace(/~/g,"~0").replace(/\//g,"~1"):"";
  const prefix=error.validationContext==="body"?"":"/"+(error.validationContext??"request");
  return {path:(prefix+(v.instancePath??"")+suffix)||"/",code:v.keyword??"invalid_value",message:"Invalid request value"};
 });
}
export function registerToolApi(app:FastifyInstance,config:ToolApiConfig,catalog:CatalogGateway):void {
 const service=createToolApiService(catalog),digest=createHash("sha256").update(config.token).digest();
 app.addHook("onRequest",async(request,reply)=>{
  const path=request.url.split("?")[0]!;
  const isApi=(request.routeOptions.config as {toolApi?:boolean}).toolApi || /^\/api(?:\/|$)/.test(path);
  if(!isApi)return;
  reply.header("cache-control","no-store");
  const header=request.headers.authorization;
  const duplicates=request.raw.rawHeaders.filter((_,i)=>i%2===0 && request.raw.rawHeaders[i]!.toLowerCase()==="authorization").length;
  const match=typeof header==="string" && header.length<=140 ? /^Bearer ([A-Za-z0-9_-]{43,128})$/i.exec(header):null;
  if(duplicates>1 || !match || !timingSafeEqual(digest,createHash("sha256").update(match[1]!).digest()))
   return reply.code(401).header("www-authenticate","Bearer").send({version:"1",request_id:request.id,status:"unauthorized",
    issues:[{path:"/headers/authorization",code:"unauthorized",message:"A valid caller Bearer token is required"}]});
 });
 app.setErrorHandler((input,request,reply)=>{
  const error:Partial<FastifyError>=typeof input==="object" && input!==null?input:{};
  let statusCode=503,issues:ContractIssue[]=[{path:"/",code:"search_unavailable",message:"Search is temporarily unavailable"}];
  let status="search_unavailable";
  if(error instanceof CatalogInputError){statusCode=400;status="invalid_request";issues=error.issues;}
  else if(error.validation){statusCode=400;status="invalid_request";issues=validationIssues(error);}
  else if(["FST_ERR_CTP_INVALID_JSON_BODY","FST_ERR_CTP_EMPTY_JSON_BODY","FST_ERR_CTP_INVALID_MEDIA_TYPE","FST_ERR_CTP_BODY_TOO_LARGE"].includes(error.code??"")){
   statusCode=error.code==="FST_ERR_CTP_BODY_TOO_LARGE"?413:error.code==="FST_ERR_CTP_INVALID_MEDIA_TYPE"?415:400;
   status="invalid_request";issues=[{path:"/",code:statusCode===413?"body_too_large":statusCode===415?"unsupported_media_type":"invalid_json",message:"Supply a valid JSON request within 64 KiB"}];
  } else app.log.error({request_id:request.id,code:databaseErrorCode(error)},"Search request failed");
  return reply.code(statusCode).send({version:"1",request_id:request.id,status,issues});
 });
 app.post("/api/search",{config:{toolApi:true},schema:{body:searchRequestSchema,querystring:emptyQuery,
   response:{200:searchResponseSchema,400:searchResponseSchema,401:httpErrorSchema,409:searchResponseSchema,413:httpErrorSchema,415:httpErrorSchema,503:searchResponseSchema}}},async(request,reply)=>{
  const response=await service.search(request.body,request.id);
  if(!validateToolSearchResponse(response).valid)throw new CatalogDataError();
  const status=response.status==="invalid_request"?400:response.status==="constraint_conflict"?409:response.status==="search_unavailable"?503:200;
  return reply.code(status).send(response);
 });
 app.get<{Params:{article_id:string}}>("/api/products/:article_id",{config:{toolApi:true},schema:{
   params:{type:"object",additionalProperties:false,required:["article_id"],properties:{article_id:{type:"string",pattern:"^1[0-9]{5}$"}}},querystring:emptyQuery,
   response:{200:detailsResponseSchema,400:httpErrorSchema,401:httpErrorSchema,404:httpErrorSchema,503:httpErrorSchema}}},async(request,reply)=>{
  const details=await service.details(Number(request.params.article_id));
  if(!details)return reply.code(404).send({version:"1",request_id:request.id,status:"not_found",
    issues:[{path:"/params/article_id",code:"product_not_found",message:"Published product not found"}]});
  const response={version:"1" as const,request_id:request.id,status:"ok" as const,details};
  if(!validDetailsResponse(response))throw new CatalogDataError();
  return reply.send(response);
 });
}

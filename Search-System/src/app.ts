import {readProductHybridConfig} from "./config/product-hybrid.config.js";
import {createProductHybridEngine,type ProductHybridEngine} from "./services/product-hybrid.service.js";
import {readCategorySemanticConfig} from "./config/category-semantic.config.js";
import {createCategorySemanticEngine,type CategorySemanticEngine} from "./services/category-semantic.service.js";
import Fastify from "fastify";
import {randomUUID} from "node:crypto";
import type {Pool} from "pg";
import {readDatabaseConfig} from "./config/database.config.js";
import {readToolApiConfig,type ToolApiConfig} from "./config/tool-api.config.js";
import {createSearchDatabase,type DatabaseLifecycle} from "./database/postgres.js";
import {createPostgresCatalogRepository} from "./repositories/postgres-catalog.repository.js";
import type {CatalogGateway} from "./services/tool-api.service.js";
import {registerToolApi} from "./http/tool-api.routes.js";
import {TOOL_AJV_OPTIONS} from "./validation/tool-search.validation.js";

export interface AppOptions {
 database?: (DatabaseLifecycle & {pool?:Pool}) | null;
 api?:ToolApiConfig|null;
 catalog?:CatalogGateway;
 categorySemantic?:CategorySemanticEngine|null;
 productHybrid?:ProductHybridEngine|null;
 logger?:boolean;
}
export function buildApp(options:AppOptions={}) {
 const api=options.api===undefined?readToolApiConfig(process.env):options.api;
 if(api)readToolApiConfig({SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:api.token});
 const app=Fastify({logger:options.logger===false?false:{redact:["req.headers.authorization","req.headers.cookie"],
   serializers:{req(request){return {method:request.method};}}},
  ajv:{customOptions:TOOL_AJV_OPTIONS},bodyLimit:64*1024,exposeHeadRoutes:false,
  requestIdHeader:false,genReqId:()=>randomUUID(),requestTimeout:20000,connectionTimeout:15000});
 const config=options.database===undefined?readDatabaseConfig(process.env):null;
 const database=options.database===undefined?(config?createSearchDatabase(config,code=>app.log.error({code},"Idle database connection failed")):null):options.database;
 if(database){
  app.addHook("onReady",async()=>{await database.check();});
  app.addHook("onClose",async()=>{await database.close();});
 }
 app.addHook("onRequest",async(request,reply)=>{reply.header("x-request-id",request.id);});
 if(api){
  const semanticConfig=options.categorySemantic===undefined?readCategorySemanticConfig(process.env):null;
  const categorySemantic=options.categorySemantic??(semanticConfig?createCategorySemanticEngine(semanticConfig):null);
  const hybridConfig=options.productHybrid===undefined?readProductHybridConfig(process.env):null;
  const productHybrid=options.productHybrid??(hybridConfig?createProductHybridEngine(hybridConfig):null);
  const catalog=options.catalog??(database?.pool?createPostgresCatalogRepository(database.pool,{categorySemantic,productHybrid}):null);
  if(!catalog)throw new Error("Enabled Tool API requires PostgreSQL catalog access");
  registerToolApi(app,api,catalog);
 }
 app.setNotFoundHandler((request,reply)=>reply.code(404).send({version:"1",request_id:request.id,status:"not_found",
  issues:[{path:"/",code:"route_not_found",message:"Route not found"}]}));
 app.get("/health",async()=>({status:"ok",service:"search-system"}));
 return app;
}

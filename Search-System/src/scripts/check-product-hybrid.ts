import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import {readDatabaseConfig} from "../config/database.config.js";
import {readProductHybridConfig} from "../config/product-hybrid.config.js";
import {createSearchDatabase,databaseErrorCode} from "../database/postgres.js";
import {createProductHybridEngine} from "../services/product-hybrid.service.js";
import {createPostgresCatalogRepository} from "../repositories/postgres-catalog.repository.js";
import {resolveCategoryFilters} from "../services/category-selector.service.js";
import {parseToolSearchRequest} from "../validation/tool-search.validation.js";
import {mapCatalogSearchResponse} from "../validation/catalog-response.mapper.js";
let database:ReturnType<typeof createSearchDatabase>|undefined;
try {
 const args=process.argv.slice(2),flags:Record<string,string>={};
 for(let i=0;i<args.length;i+=2){const key=args[i]!;if(!["--category-id","--query","--semantic-query","--max-price"].includes(key) || key in flags || args[i+1]===undefined)throw new Error("Invalid check flags");flags[key]=args[i+1]!;}
 if(!flags["--category-id"] || !flags["--semantic-query"])throw new Error("Specify category and intent");
 const rawPrice=flags["--max-price"];
 if(rawPrice!==undefined && (rawPrice.length>20 || !/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(rawPrice)))throw new Error("Invalid price flag");
 if(rawPrice!==undefined && Number(rawPrice)>Number.MAX_SAFE_INTEGER)throw new Error("Price flag too large");
 const price=rawPrice===undefined?{}:{price:{max:Number(rawPrice),currency:"THB"}};
 const parsed=parseToolSearchRequest({version:"1",query:flags["--query"]??"",semantic_query:flags["--semantic-query"],filters:{category_ids:[flags["--category-id"]],...price},limit:5});
 if(!parsed.valid)throw new Error("Invalid check request");
 const env={...parse(await readFile(".env","utf8")),...parse(await readFile(".env.embedding.local","utf8")),...parse(await readFile(".env.product-hybrid.local","utf8"))};
 const config=readProductHybridConfig(env),dbconfig=readDatabaseConfig(env);if(!config || !dbconfig)throw new Error("Enable hybrid and PostgreSQL");
 database=createSearchDatabase(dbconfig);await database.check();const engine=createProductHybridEngine(config);
 const client=await database.pool.connect();let state;
 try{await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");const scope=await resolveCategoryFilters(client,parsed.value.filters);
  state=scope.status==="resolved"?await engine.readiness(client,scope.category_ids,parsed.value):{status:"filters_unresolved",total:0,fresh:0};await client.query("COMMIT");
 }catch(e){await client.query("ROLLBACK").catch(()=>{});throw e;}finally{client.release();}
 const snapshot=await createPostgresCatalogRepository(database.pool,{productHybrid:engine}).search(parsed.value);
 const response=mapCatalogSearchResponse(snapshot,"hybrid-check");
 console.log(JSON.stringify({status:snapshot.search?.semantic_available?"product_hybrid_ready":"product_hybrid_fallback",coverage:state,response,database_writes:0},null,2));
}catch(e){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(e),message:"ตรวจ migration0010 สิทธิ์อ่านและ profile ส่วนตัว ไม่แสดงรหัสผ่าน เวกเตอร์ หรือข้อความ SQL"}));process.exitCode=1;}
finally{await database?.close();}

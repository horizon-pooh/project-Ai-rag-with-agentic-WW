import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import {readCategorySemanticConfig} from "../config/category-semantic.config.js";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase,databaseErrorCode} from "../database/postgres.js";
import {createCategorySemanticEngine} from "../services/category-semantic.service.js";
import {resolveCategoryFilters} from "../services/category-selector.service.js";
let database:ReturnType<typeof createSearchDatabase>|undefined;
try {
 const args=process.argv.slice(2);if(args.length!==2 || args[0]!=="--query" || !args[1]?.trim() || [...args[1]].length>200)throw new Error("Require --query and a bounded phrase");
 const env={...parse(await readFile(".env","utf8")),...parse(await readFile(".env.embedding.local","utf8")),...parse(await readFile(".env.category-semantic.local","utf8"))};
 const config=readCategorySemanticConfig(env),dbconfig=readDatabaseConfig(env);if(!config || !dbconfig)throw new Error("Enable private semantic/DB configuration");
 database=createSearchDatabase(dbconfig);await database.check();const engine=createCategorySemanticEngine(config);
 const snapshot=async<T>(action:(db:import("pg").PoolClient)=>Promise<T>)=>{
  const c=await database!.pool.connect();try{await c.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");const result=await action(c);await c.query("COMMIT");return result;}
  catch(e){await c.query("ROLLBACK").catch(()=>{});throw e;}finally{c.release();}
 };
 const first=await snapshot(async db=>({lexical:await resolveCategoryFilters(db,{category_query:args[1]!}),state:await engine.readiness(db)}));
 const prepared=engine.hasWork(first.lexical)?await engine.prepare(first.lexical,first.state):null;
 const resolved=await snapshot(async db=>{const current=await resolveCategoryFilters(db,{category_query:args[1]!});return prepared?engine.resolve(db,current,prepared):current;});
 console.log(JSON.stringify({status:first.state.status==="ready"?"category_semantic_ready":"category_semantic_fallback",coverage:first.state,
  mode:config.policy.autoSelect?"calibrated_auto":"suggestions",resolution:resolved,product_vector_ranking:false,database_writes:0},null,2));
}catch(e){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(e),message:"Check migration0008, search-role access and private semantic/profile configuration; no credentials/vector/raw errors printed."}));process.exitCode=1;}
finally{await database?.close();}

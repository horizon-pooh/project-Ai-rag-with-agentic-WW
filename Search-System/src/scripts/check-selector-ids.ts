import "dotenv/config";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase,databaseErrorCode} from "../database/postgres.js";
import {parseToolSearchRequest} from "../validation/tool-search.validation.js";
import {resolveCategoryFilters} from "../services/category-selector.service.js";
import {createTypedFilterRepository} from "../repositories/typed-filter.repository.js";
import type {ToolSearchFilters} from "../types/tool-search.types.js";

async function main():Promise<void>{
 const config=readDatabaseConfig(process.env);if(!config)throw new Error("Set DATA_SOURCE=postgres before checking selector IDs");
 const database=createSearchDatabase(config);
 try{
  await database.check();const client=await database.pool.connect();
  try{
   await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
   const typed=createTypedFilterRepository(client);const samples=[];
   for(const file of ["request-with-ids.json","request-ids-only.json"]){
    const input=JSON.parse(await readFile("docs/examples/tool-search-v1/"+file,"utf8"));const parsed=parseToolSearchRequest(input);
    assert.equal(parsed.valid,true);if(!parsed.valid)throw new Error("Invalid example contract");
    const scope=await resolveCategoryFilters(client,parsed.value.filters);assert.equal(scope.status,"resolved");
    assert.deepEqual(scope.category_ids,["TV0108"]);const result=await typed.resolve(scope,parsed.value.filters);
    assert.equal(result.status,"resolved");assert.equal(result.all_requested_filters_applied,true);
    assert.deepEqual(result.branches[0]!.feature_ids_all,["FT0023"]);
    assert.equal(result.branches[0]!.resolved_specs[0]!.numeric_value,"55");samples.push({file,scope,result});
   }
   const legacy:ToolSearchFilters={category_query:"ทีวี OLED",feature_queries_all:["HDMI input"],specs:[{attribute_query:"ขนาดหน้าจอ",op:"eq",value:55,unit:"นิ้ว"}]};
   const legacyScope=await resolveCategoryFilters(client,legacy);const old=await typed.resolve(legacyScope,legacy);
   assert.equal(old.status,"resolved");assert.equal(old.resolver_version,"typed-filters-v1");
   const unknown=await resolveCategoryFilters(client,{category_ids:["ZX9999"]});assert.equal(unknown.status,"needs_clarification");
   const conflict=await resolveCategoryFilters(client,{category_query:"ทีวี OLED",category_ids:["TV0109"]});assert.equal(conflict.status,"constraint_conflict");
   console.log(JSON.stringify({status:"selector_ids_ready",samples,legacy:old,unknown,conflict},null,2));
  }finally{try{await client.query("ROLLBACK");}finally{client.release();}}
 }finally{await database.close();}
}
try{await main();}catch(error){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
 message:"Check migration 0005, saved code/examples, tunnel and reader access. Do not paste secrets."}));process.exitCode=1;}

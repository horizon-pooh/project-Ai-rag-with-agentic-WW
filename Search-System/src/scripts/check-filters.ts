import "dotenv/config";
import assert from "node:assert/strict";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase,databaseErrorCode} from "../database/postgres.js";
import {createCategoryResolutionRepository} from "../repositories/category-resolution.repository.js";
import {resolveCategoryQuery} from "../services/category-resolution.service.js";
import {createTypedFilterRepository} from "../repositories/typed-filter.repository.js";

async function main():Promise<void>{
 const config=readDatabaseConfig(process.env);if(!config)throw new Error("Set DATA_SOURCE=postgres before checking filters");
 const database=createSearchDatabase(config);
 try{
  await database.check();const client=await database.pool.connect();
  try{
   await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
   const categories=createCategoryResolutionRepository(client),filters=createTypedFilterRepository(client);
   const tv=await resolveCategoryQuery(categories,"ทีวี OLED");
   const example=await filters.resolve(tv,{category_query:"ทีวี OLED",brand_queries:["LG"],price:{max:25000},
    feature_queries_all:["HDMI input"],specs:[{attribute_query:"ขนาดหน้าจอ",op:"eq",value:55,unit:"นิ้ว"},
     {attribute_query:"quantum_dot",op:"eq",value:false}]});
   assert.equal(example.status,"resolved");assert.equal(example.all_requested_filters_applied,true);
   assert.deepEqual(example.branches[0]!.brand_ids,["BR0002"]);assert.deepEqual(example.branches[0]!.feature_ids_all,["FT0023"]);
   assert.equal(example.branches[0]!.resolved_specs[0]!.numeric_value,"55");
   assert.equal(example.branches[0]!.resolved_specs[1]!.boolean_value,false);
   const conflict=await filters.resolve(tv,{specs:[{attribute_query:"width",op:"gte",value:100},{attribute_query:"width",op:"lte",value:50}]});
   assert.equal(conflict.status,"constraint_conflict");
   const mismatch=await filters.resolve(tv,{specs:[{attribute_query:"screen_size",op:"eq",value:"55"}]});
   assert.equal(mismatch.branches[0]!.unapplied_filters[0]!.reason,"data_type_mismatch");
   const laundry=await resolveCategoryQuery(categories,"AP0106");
   const ambiguous=await filters.resolve(laundry,{specs:[{attribute_query:"ระบบอบ",op:"eq",value:true}]});
   assert.equal(ambiguous.status,"needs_clarification");
   const converted=await filters.resolve(tv,{specs:[{attribute_query:"width",op:"eq",value:100,unit:"mm"}]});
   assert.equal(Number(converted.branches[0]!.resolved_specs[0]!.numeric_value),10);
   console.log(JSON.stringify({status:"typed_filters_ready",example,conflict,mismatch,ambiguous,converted},null,2));
  }finally{try{await client.query("ROLLBACK");}finally{client.release();}}
 }finally{await database.close();}
}
try{await main();}catch(error){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
 message:"Check migration 0004, metadata, tunnel and reader access. No credentials should be pasted."}));process.exitCode=1;}

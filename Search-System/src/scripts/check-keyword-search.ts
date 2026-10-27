import "dotenv/config";
import assert from "node:assert/strict";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase,databaseErrorCode} from "../database/postgres.js";
import {parseToolSearchRequest} from "../validation/tool-search.validation.js";
import {resolveCategoryFilters} from "../services/category-selector.service.js";
import type {ToolSearchRequest} from "../types/tool-search.types.js";
import type {KeywordSearchEnvelope} from "../types/keyword-search.types.js";

async function main():Promise<void>{
 const config=readDatabaseConfig(process.env);if(!config)throw new Error("Set DATA_SOURCE=postgres before checking keyword search");
 const database=createSearchDatabase(config);
 try{
  await database.check();const client=await database.pool.connect();
  try{
   await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
   const run=async(input:ToolSearchRequest):Promise<KeywordSearchEnvelope>=>{
    const parsed=parseToolSearchRequest(input);assert.equal(parsed.valid,true);if(!parsed.valid)throw new Error("Invalid smoke request");
    const scope=await resolveCategoryFilters(client,parsed.value.filters);assert.equal(scope.status,"resolved");
    const result=await client.query<{result:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb) AS result",[scope.category_ids,JSON.stringify(parsed.value)]);
    return result.rows[0]!.result;
   };
   const browse=await run({filters:{category_ids:["TV0108"]},sort:"id",limit:5});
   assert.equal(browse.status,"ok");assert.ok(browse.meta!.total_matches>0);assert.equal(browse.meta!.total_alternatives,0);
   const article=browse.results[0]!.article_id;
   const example=await run({query:"ทีวี",semantic_query:"ทีวีสำหรับดูหนัง",filters:{category_query:"ทีวี OLED",category_ids:["TV0108"],
    feature_ids_all:["FT0023"],specs:[{attribute_query:"ขนาดหน้าจอ",attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}],price:{max:25000}},limit:5});
   assert.equal(example.semantic_available,false);assert.ok(example.warnings.some(w=>w.code==="semantic_unavailable"));
   let alternative=false;for(const hit of example.results){
    assert.equal(hit.category_id,"TV0108");assert.ok(hit.mock_notice || !hit.is_mock);
    if(hit.match_type==="alternative"){alternative=true;assert.ok(hit.unmet_filters.length+hit.unknown_filters.length>0);}
    else assert.equal(alternative,false);
   }
   const exact=await run({exact:{article_id:article},filters:{category_ids:["TV0108"],price:{max:0}},limit:100});
   assert.deepEqual(exact.results.map(p=>p.article_id),[article]);
   const draft=await run({exact:{article_id:106045},filters:{category_ids:["TV0108"]}});assert.equal(draft.status,"no_matches");
   const exhausted=await run({filters:{category_ids:["TV0108"]},page:browse.meta!.total_matches+1,limit:1});
   assert.equal(exhausted.status,"ok");assert.equal(exhausted.results.length,0);assert.equal(exhausted.meta!.has_more,false);
   console.log(JSON.stringify({status:"keyword_sql_ready",browse,example,exact,draft,exhausted},null,2));
  }finally{try{await client.query("ROLLBACK");}finally{client.release();}}
 }finally{await database.close();}
}
try{await main();}catch(error){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
 message:"Check migration 0006, schema, published data, tunnel and reader access. Do not paste credentials."}));process.exitCode=1;}

import "dotenv/config";
import assert from "node:assert/strict";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase,databaseErrorCode} from "../database/postgres.js";
import {createPostgresCatalogRepository} from "../repositories/postgres-catalog.repository.js";
import {mapCatalogSearchResponse} from "../validation/catalog-response.mapper.js";

async function main():Promise<void> {
  const config=readDatabaseConfig(process.env);if(!config)throw new Error("Set DATA_SOURCE=postgres");
  const database=createSearchDatabase(config);
  try {
    await database.check();const repository=createPostgresCatalogRepository(database.pool);
    const snapshot=await repository.search({query:"ทีวี",semantic_query:"ทีวีสำหรับดูหนัง",filters:{category_query:"ทีวี OLED",category_ids:["TV0108"],
      price:{max:25000},specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}]},limit:5});
    const response=mapCatalogSearchResponse(snapshot,"catalog-repository-smoke");assert.equal(response.status,"ok");
    if(response.status!=="ok")throw new Error("Unexpected response");
    assert.ok(response.results.length>0);assert.ok(response.results.every(r=>r.product.category_id==="TV0108"));
    assert.equal(response.semantic_available,false);
    const article=response.results[0]!.product.article_id,details=await repository.findDetails(article);assert.ok(details);
    assert.equal(details.product.article_id,article);assert.ok(details.content_sections.length>0);
    assert.ok(details.specifications.some(s=>s.attribute_id==="AT0048" && s.value!==null));
    assert.equal(await repository.findDetails(106045),null); // known draft from the imported baseline
    assert.equal(await repository.findDetails(199999),null); // outside the imported 18,000 Articles
    const exhausted=mapCatalogSearchResponse(await repository.search({filters:{category_ids:["TV0108"]},page:100,limit:1}),"exhausted");
    assert.equal(exhausted.status,"ok");if(exhausted.status!=="ok")throw new Error("Unexpected exhausted status");
    assert.equal(exhausted.results.length,0);assert.equal(exhausted.meta.has_more,false);
    console.log(JSON.stringify({status:"catalog_repository_ready",search:response,
      details:{article_id:article,specifications:details.specifications.length,features:details.feature_details.length,
        sections:details.content_sections.map(s=>({section_type:s.section_type,applicability:s.applicability}))},
      draft_visible:false,missing_visible:false,exhausted_page:exhausted.meta},null,2));
  } finally { await database.close(); }
}
try { await main(); } catch(error) {
  console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
    message:"Check migration 0006, reader access, catalog facts and tunnel. Never paste credentials or raw database errors."}));
  process.exitCode=1;
}

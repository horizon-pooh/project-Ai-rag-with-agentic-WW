import "dotenv/config";
import assert from "node:assert/strict";
import { readDatabaseConfig } from "../config/database.config.js";
import { createSearchDatabase,databaseErrorCode } from "../database/postgres.js";
import { createCategoryResolutionRepository } from "../repositories/category-resolution.repository.js";
import { resolveCategoryQuery } from "../services/category-resolution.service.js";

async function main():Promise<void>{
  const config=readDatabaseConfig(process.env);
  if(!config) throw new Error("Set DATA_SOURCE=postgres before checking categories");
  const database=createSearchDatabase(config);
  try {
    await database.check();const client=await database.pool.connect();
    try {
      await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
      const repository=createCategoryResolutionRepository(client);
      const results=[];
      for(const query of ["ทีวี","TV","โทรทัศน์","ทีวี OLED","TV0109","ทีวี OLED หรือ TV0109","Televsions","ZX9999"])
        results.push(await resolveCategoryQuery(repository,query));
      const [broad,tv,thai,oled,qled,multiple,typo,unknown]=results;
      assert.equal(broad!.status,"resolved");assert.ok(broad!.category_ids.includes("TV0108"));
      assert.deepEqual(tv!.category_ids,broad!.category_ids);assert.deepEqual(thai!.category_ids,broad!.category_ids);
      assert.deepEqual(oled!.category_ids,["TV0108"]);assert.deepEqual(qled!.category_ids,["TV0109"]);
      assert.deepEqual(multiple!.category_ids,["TV0108","TV0109"]);
      assert.equal(typo!.status,"needs_clarification");assert.equal(unknown!.status,"needs_clarification");
      const summary=results.map(r=>({query:r.query,status:r.status,category_ids:r.category_ids,
        reasons:r.groups.map(g=>g.reason),candidates:r.groups.map(g=>g.candidates.map(c=>({
          id:c.category_id,label:c.name_th,matched_label:c.matched_label,kind:c.match_kind,score:c.score})))}));
      console.log(JSON.stringify({status:"category_resolution_ready",resolver_version:"category-lexical-v1",results:summary},null,2));
    }finally{try{await client.query("ROLLBACK");}finally{client.release();}}
  }finally{await database.close();}
}
try{await main();}catch(error){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
  message:"Check migration 0003, category hierarchy, pg_trgm schema, SSH tunnel and reader access. Do not paste secrets."}));process.exitCode=1;}

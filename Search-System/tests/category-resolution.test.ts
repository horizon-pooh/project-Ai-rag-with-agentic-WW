import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp,readFile,copyFile,rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join,resolve } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { pg_trgm } from "@electric-sql/pglite/contrib/pg_trgm";
import type { ClientBase,PoolClient } from "pg";
import { applyMigrations } from "../src/database/migrations.js";
import { createCategoryResolutionRepository } from "../src/repositories/category-resolution.repository.js";
import { resolveCategoryQuery,splitCategoryQuery } from "../src/services/category-resolution.service.js";
import type { CategoryResolutionRepository } from "../src/types/category-resolution.types.js";

test("category lexical resolver, scope integrity and migration on PostgreSQL",async t=>{
  const db=new PGlite({extensions:{pg_trgm}});
  const directory=await mkdtemp(join(tmpdir(),"category-resolution-"));
  const statements:string[]=[];
  const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
    const sql=typeof query==="string"?query:query.text;
    const args=values ?? (typeof query==="object"?query.values:undefined);
    statements.push(sql);
    const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);
    return {rows:r?.rows ?? [],rowCount:r?.affectedRows ?? 0};
  }} as unknown as ClientBase;
  const repository=createCategoryResolutionRepository(client as unknown as PoolClient);
  const resolveQuery=(query:string)=>resolveCategoryQuery(repository,query);
  const reader=async(action:()=>Promise<void>)=>{
    await db.exec("SET ROLE search_api");try {await action();}finally{await db.exec("RESET ROLE");}
  };
  try {
    await db.exec(await readFile(resolve("tests/fixtures/metadata.sql"),"utf8"));
    await db.exec(await readFile(resolve("tests/fixtures/category-resolution.sql"),"utf8"));
    await db.exec("CREATE SCHEMA extensions; CREATE EXTENSION pg_trgm WITH SCHEMA extensions; GRANT USAGE ON SCHEMA extensions TO catalog_search_reader");
    for(const name of ["0002_metadata_helpers.sql","0003_category_resolution.sql"])
      await copyFile(resolve("migrations",name),join(directory,name));
    await t.test("real runner applies metadata and resolver migrations, replay is no-op",async()=>{
      assert.deepEqual(await applyMigrations(client,directory,"up"),["0002_metadata_helpers","0003_category_resolution"]);
      assert.deepEqual(await applyMigrations(client,directory,"up"),[]);
    });
    for(const query of ["ทีวี","TV"," tv ","โทรทัศน์","Televisions","TV0100"]){
      await t.test(`broad category expands TV leaves only: ${query}`,async()=>reader(async()=>{
        const result=await resolveQuery(query);
        assert.equal(result.status,"resolved");
        assert.deepEqual(result.category_ids,["TV0107","TV0108","TV0109"]);
        assert.ok(result.scopes.every(s=>s.path_ids[0]==="TV0000" && s.path_ids[1]==="TV0100"));
      }));
    }
    for(const query of ["OLED","OLED TV","oled television","ทีวี OLED","ทีวีOLED","TV0108","tv0108"]){
      await t.test(`specific OLED excludes QLED and accessories: ${query}`,async()=>{
        const r=await resolveQuery(query);assert.equal(r.status,"resolved");assert.deepEqual(r.category_ids,["TV0108"]);
      });
    }
    for(const [query,expected] of [["QLED",["TV0109"]],["NEO QLED",["TV0107"]],
      ["ขาแขวนทีวี",["TV0201"]],["อุปกรณ์เสริมทีวี",["TV0201","TV0202"]]] as const){
      await t.test(`exact specificity: ${query}`,async()=>assert.deepEqual((await resolveQuery(query)).category_ids,expected));
    }
    await t.test("multiple explicit OR groups merge leaves without duplicates",async()=>{
      const r=await resolveQuery("ทีวี หรือ OLED | เครื่องซักผ้า");
      assert.equal(r.status,"resolved");assert.deepEqual(r.category_ids,["AP0101","TV0107","TV0108","TV0109"]);
      assert.equal(r.groups.length,3);
    });
    await t.test("English OR is explicit, contains no substring split of words",async()=>{
      assert.deepEqual(splitCategoryQuery("OLED or QLED"),["OLED","QLED"]);
      assert.deepEqual(splitCategoryQuery("door"),["door"]);
      assert.deepEqual((await resolveQuery("OLED or QLED")).category_ids,["TV0108","TV0109"]);
    });
    await t.test("same leaf scope across parent/child duplicate names is safely coalesced",async()=>{
      const r=await resolveQuery("อาหารและเครื่องดื่ม");assert.equal(r.status,"resolved");
      assert.deepEqual(r.category_ids,["FO0101"]);assert.equal(r.groups[0]!.reason,"equivalent_scopes");
      assert.deepEqual(r.groups[0]!.equivalent_root_ids,["FO0000","FO0100"]);
    });
    await t.test("ambiguous exact alias keeps all candidates and selects no partial scope",async()=>{
      const r=await resolveQuery("จอพิเศษ");assert.equal(r.status,"needs_clarification");
      assert.equal(r.groups[0]!.reason,"ambiguous_exact_label");assert.equal(r.groups[0]!.candidates.length,2);
      assert.deepEqual(r.category_ids,[]);assert.deepEqual(r.scopes,[]);
    });
    for(const query of ["Televsions","OELD TV","ทีวี OELD"]){
      await t.test(`typo suggestions cannot silently select a different panel: ${query}`,async()=>{
        const r=await resolveQuery(query);assert.equal(r.status,"needs_clarification");
        assert.ok(r.groups[0]!.candidates.length>0);assert.equal(r.groups[0]!.reason,"similar_labels_require_confirmation");
        assert.deepEqual(r.category_ids,[]);
      });
    }
    for(const query of ["ZX9999","xyzzyxnomatch","OLED/QLED","ทีวี OLED 55 นิ้ว"]){
      await t.test(`unresolved text never falls back to whole catalog: ${query}`,async()=>{
        const r=await resolveQuery(query);assert.equal(r.status,"needs_clarification");assert.deepEqual(r.category_ids,[]);
      });
    }
    await t.test("one unknown OR branch prevents silently dropping that requirement",async()=>{
      const r=await resolveQuery("OLED | xyzzyxnomatch");assert.equal(r.status,"needs_clarification");
      assert.equal(r.groups[0]!.status,"resolved");assert.deepEqual(r.category_ids,[]);
    });
    await t.test("empty category scope is clarification rather than product no_matches",async()=>{
      const r=await resolveQuery("หมวดว่าง");assert.equal(r.status,"needs_clarification");
      assert.equal(r.groups[0]!.reason,"empty_category_scope");
    });
    await t.test("invalid requests and SQL parameters are bounded",async()=>{
      for(const q of [" ","a".repeat(201),"OLED |", "a|b|c|d|e|f"])
        await assert.rejects(resolveQuery(q));
      await assert.rejects(repository.findCandidates([]));
      await assert.rejects(repository.expandRoots(["invalid"]));
      await assert.rejects(db.query("SELECT * FROM catalog_search.category_candidates('tv',99)"));
      await assert.rejects(db.query("SELECT * FROM catalog_search.category_leaf_scopes(ARRAY['XX9999'])"));
      await assert.rejects(db.query("SELECT * FROM catalog_search.category_leaf_scopes(ARRAY[NULL]::text[])"));
    });
    await t.test("parameterized label cannot inject SQL",async()=>{
      const r=await resolveQuery("'; DROP TABLE categories; --");assert.equal(r.status,"needs_clarification");
      assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM categories")).rows[0]!.n,16);
    });
    await t.test("at most two bounded SQL calls, no product reads or per-leaf queries",async()=>{
      statements.length=0;await resolveQuery("ทีวี | เครื่องซักผ้า");
      assert.equal(statements.length,2);assert.ok(statements.every(s=>!s.includes("products")));
      assert.ok(statements[0]!.includes("$1::text[]"));
    });
    await t.test("unknown query skips expansion SQL; database errors propagate",async()=>{
      statements.length=0;await resolveQuery("xyzzyxnomatch");assert.equal(statements.length,1);
      const broken:CategoryResolutionRepository={async findCandidates(){throw new Error("database unavailable");},async expandRoots(){return [];}};
      await assert.rejects(resolveCategoryQuery(broken,"ทีวี"),/database unavailable/);
    });
    await t.test("explicit unknown ID cannot be redirected by an alias",async()=>{
      await db.exec("UPDATE categories SET aliases=aliases||ARRAY['ZX9999'] WHERE id='TV0108'");
      const r=await resolveQuery("ZX9999");assert.equal(r.status,"needs_clarification");
      assert.deepEqual(r.category_ids,[]);assert.deepEqual(r.groups[0]!.candidates,[]);
    });
    await t.test("candidate truncation cannot silently choose a partial set",async()=>{
      await db.exec("UPDATE categories SET aliases=aliases||ARRAY['คำร่วมมาก']");
      const r=await resolveQuery("คำร่วมมาก");assert.equal(r.status,"needs_clarification");
      assert.equal(r.groups[0]!.candidates.length,8);assert.equal(r.groups[0]!.candidate_total,16);
      assert.equal(r.groups[0]!.reason,"candidate_limit_exceeded");assert.deepEqual(r.category_ids,[]);
    });
    await t.test("results and tie ordering are deterministic",async()=>{
      assert.deepEqual(await resolveQuery("OELD TV"),await resolveQuery("OELD TV"));
    });
    await t.test("reader can execute, anon cannot, and no source writes are granted",async()=>{
      await reader(async()=>{await resolveQuery("ทีวี");await assert.rejects(db.exec("UPDATE categories SET name_th=name_th"));});
      await db.exec("SET ROLE anon");try {await assert.rejects(resolveQuery("ทีวี"));}finally{await db.exec("RESET ROLE");}
    });
    await t.test("broken hierarchy is an operational error, not truncated success",async()=>{
      await db.exec("UPDATE categories SET parent_id='TV0108' WHERE id='TV0100'");
      await assert.rejects(resolveQuery("ทีวี"),(e:unknown)=>typeof e==="object" && e!==null && "code" in e && e.code==="55000");
      await db.exec("UPDATE categories SET parent_id='TV0000' WHERE id='TV0100'");
    });
    await t.test("rollback leaves source metadata, Commit 11 and pg_trgm intact",async()=>{
      const before=await resolveQuery("ทีวี");
      assert.deepEqual(await applyMigrations(client,directory,"down"),["0003_category_resolution"]);
      assert.ok((await db.query("SELECT catalog_search.metadata_version() version")).rows[0]);
      assert.ok((await db.query("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'")).rows[0]);
      assert.deepEqual(await applyMigrations(client,directory,"up"),["0003_category_resolution"]);
      assert.deepEqual(await resolveQuery("ทีวี"),before);
    });
  }finally{await db.close();await rm(directory,{recursive:true,force:true});}
});

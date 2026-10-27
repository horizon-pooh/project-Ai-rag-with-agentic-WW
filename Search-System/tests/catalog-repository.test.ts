import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {join,resolve} from "node:path";
import {tmpdir} from "node:os";
import {PGlite} from "@electric-sql/pglite";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import type {ClientBase,PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import {createProductFactsRepository,decimalFact} from "../src/repositories/product-facts.repository.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import {mapCatalogSearchResponse} from "../src/validation/catalog-response.mapper.js";
import {CatalogDataError,CatalogInputError,type CatalogPool} from "../src/types/catalog-read.types.js";
import {validateToolSearchResponse} from "../src/validation/tool-search.validation.js";
import type {ToolSearchRequest} from "../src/types/tool-search.types.js";

test("bounded PostgreSQL catalog reads and v1 mapping",async t=>{
 const db=new PGlite({extensions:{pg_trgm}}),directory=await mkdtemp(join(tmpdir(),"catalog-repository-"));
 const queries:Array<{sql:string;args?:unknown[]}>=[];const releases:boolean[]=[];
 let checkedOut=0;
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
  const sql=typeof query==="string"?query:query.text,args=values??(typeof query==="object"?query.values:undefined);
  queries.push({sql,args});const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);
  return {rows:r?.rows??[],rowCount:r?.affectedRows??0};
 },release(destroy=false){releases.push(destroy);}} as unknown as PoolClient;
 const pool={async connect(){checkedOut++;return client;}} as CatalogPool;
 const repo=createPostgresCatalogRepository(pool),facts=createProductFactsRepository(client);
 const search=(input:ToolSearchRequest={})=>repo.search({filters:{category_ids:["TV0108"]},...input});
 const response=async(input:ToolSearchRequest={})=>mapCatalogSearchResponse(await search(input),"catalog-test");
 try{
  for(const file of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql"])
   await db.exec(await readFile(resolve("tests/fixtures",file),"utf8"));
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const name of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql"])
   await copyFile(resolve("migrations",name),join(directory,name));
  await applyMigrations(client as unknown as ClientBase,directory,"up");
  await db.exec("SET ROLE search_api");
  await t.test("hydrates facts for returned Articles only, in SQL rank order",async()=>{
   queries.length=0;const r=await search({sort:"price_desc",limit:3});
   assert.deepEqual(r.products.map(p=>p.article_id),r.search!.results.map(p=>p.article_id));
   assert.equal(r.products.length,3);
   const hydration=queries.filter(q=>q.sql.includes("p.price::text AS price_decimal"));
   assert.equal(hydration.length,1);assert.deepEqual(hydration[0]!.args,[r.products.map(p=>p.article_id)]);
   assert.equal(queries.filter(q=>q.sql.includes("SELECT pf.product_id")).length,1);
   assert.equal(queries.filter(q=>q.sql.includes("WITH selected AS MATERIALIZED")).length,1);
   assert.ok(queries[0]!.sql.includes("REPEATABLE READ READ ONLY"));assert.equal(queries.at(-1)!.sql,"COMMIT");
   assert.ok(!queries.some(q=>q.sql.includes("FROM public.product_content_sections")));
  });
  await t.test("100-row limit does not create per-product spec/feature queries",async()=>{
   queries.length=0;await search({limit:100});
   assert.equal(queries.filter(q=>q.sql.includes("WITH selected AS MATERIALIZED")).length,1);
   assert.equal(queries.filter(q=>q.sql.includes("SELECT pf.product_id")).length,1);
  });
  await t.test("batch removes duplicate IDs and retains first requested order",async()=>{
   const rows=await facts.findMany([100001,100000,100001,199999]);assert.deepEqual(rows.map(p=>p.article_id),[100001,100000]);
  });
  await t.test("empty batch executes no SQL",async()=>{queries.length=0;assert.deepEqual(await facts.findMany([]),[]);assert.equal(queries.length,0);});
  for(const bad of [["100000"],[99999],[200000],[100000.5],Array(101).fill(100000),null])
   await t.test("rejects invalid or unbounded batch "+JSON.stringify(bad)?.slice(0,30),async()=>{
    queries.length=0;await assert.rejects(()=>facts.findMany(bad as number[]),TypeError);assert.equal(queries.length,0);
   });
  await t.test("boolean false, zero and Thai text survive hydration",async()=>{
   const [a,z]=await facts.findMany([100000,100007]);assert.equal(z!.price,0);
   assert.equal(a!.specifications.find(s=>s.attribute_id==="AT0093")!.value,false);
   assert.equal(a!.specifications.find(s=>s.attribute_id==="AT0048")!.value,55);
   assert.equal(a!.specifications.find(s=>s.attribute_id==="AT0048")!.unit,"inch");assert.ok(a!.mock_notice);
  });
  for(const id of [100003,100009,100010])await t.test("missing/nonfinite/wrong typed spec stays null "+id,async()=>{
   const [p]=await facts.findMany([id]);assert.equal(p!.specifications.find(s=>s.attribute_id==="AT0048")!.value,null);
  });
  await t.test("required category definition without recorded value is explicit null",async()=>{
   const [p]=await facts.findMany([100000]);assert.equal(p!.specifications.find(s=>s.attribute_id==="AT0010")!.value,null);
  });
  await t.test("recorded non-filterable text is included without unrelated category attributes",async()=>{
   const [p]=await facts.findMany([100000]);assert.equal(p!.specifications.find(s=>s.attribute_id==="AT0014")!.value,"Wood");
   assert.ok(!p!.specifications.some(s=>s.attribute_id==="AT0099"));
  });
  await t.test("details preserve structured content, applicability, ordering and feature descriptions",async()=>{
   const d=await repo.findDetails(100000);assert.ok(d);assert.equal(d.series,"Cinema");assert.equal(d.regular_price,22000);
   assert.equal(d.content_sections.length,2);assert.equal(d.content_sections[1]!.applicability,"not_applicable");
   assert.deepEqual(d.content_sections[1]!.items,[{reason:"ไม่ต้องประกอบ"}]);assert.ok(d.feature_details[0]!.benefit);
   assert.equal(d.specifications.find(s=>s.attribute_id==="AT0048")!.numeric_decimal,"55");
  });
  for(const id of [106045,199999])await t.test("draft and missing details both return null "+id,async()=>{
   queries.length=0;assert.equal(await repo.findDetails(id),null);assert.equal(queries.length,3); // BEGIN, product lookup, COMMIT
  });
  await t.test("mixed batch cannot reveal draft facts or draft child rows",async()=>{
   const rows=await facts.findMany([106045,100000]);assert.deepEqual(rows.map(p=>p.article_id),[100000]);
  });
  await t.test("invalid input does not acquire a connection",async()=>{
   const before=checkedOut;await assert.rejects(()=>repo.search({query:"ทีวี",limit:"5"}),CatalogInputError);
   await assert.rejects(()=>repo.findDetails("100000" as unknown as number),TypeError);assert.equal(checkedOut,before);
  });
  await t.test("unresolved category returns clarification with no keyword/fact SQL",async()=>{
   queries.length=0;const r=await response({filters:{category_query:"unknownunknowncategory"}});
   assert.equal(r.status,"needs_clarification");assert.ok(!queries.some(q=>q.sql.includes("search_products_keyword")));
  });
  await t.test("missing category scope requests clarification, never scans entire catalog",async()=>{
   const r=mapCatalogSearchResponse(await repo.search({query:"ทีวี"}),"scope");assert.equal(r.status,"needs_clarification");
  });
  await t.test("category selector conflict maps to public error contract",async()=>{
   const r=await response({filters:{category_query:"ทีวี OLED",category_ids:["TV0109"]}});assert.equal(r.status,"constraint_conflict");
  });
  await t.test("legacy human names remain usable and schema-valid",async()=>{
   const r=await response({query:"ทีวี",filters:{category_query:"ทีวี OLED",specs:[{attribute_query:"ขนาดหน้าจอ",op:"eq",value:55,unit:"นิ้ว"}]}});
   assert.equal(r.status,"ok");assert.equal(validateToolSearchResponse(r).valid,true);
  });
  await t.test("strict plus alternatives maps to v1 with original paths and semantic warning",async()=>{
   const r=await response({query:"ทีวี",semantic_query:"ทีวีดูหนัง",filters:{category_ids:["TV0108"],price:{max:25000},
    specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"inch"}]},limit:100});
   assert.equal(r.status,"ok");if(r.status!=="ok")return;
   assert.ok(r.results.some(p=>p.match_type==="alternative"));assert.ok(r.results.some(p=>p.match_type==="matches_applied_filters"));
   assert.equal(r.semantic_available,false);assert.equal(validateToolSearchResponse(r).valid,true);
  });
  await t.test("unknown values remain null in public diagnostics",async()=>{
   const r=await response({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]},limit:100});
   if(r.status!=="ok")assert.fail();const p=r.results.find(p=>p.product.article_id===100003)!;
   assert.equal(p.unknown_filters[0]!.actual_value,null);assert.equal(p.product.specifications.find(s=>s.attribute_id==="AT0048")!.value,null);
  });
  await t.test("converted diagnostic values are labelled canonical units",async()=>{
   const r=await response({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:139.7,unit:"cm"}]},limit:100});
   if(r.status!=="ok")assert.fail();const a=r.results.find(p=>p.product.article_id===100001)!;
   assert.match(a.unmet_filters[0]!.reason,/values in inch/);assert.match(a.unmet_filters[0]!.reason,/input 139.7 cm/);
   assert.ok(Math.abs(Number(a.unmet_filters[0]!.requested_value)-55)<1e-9);
  });
  await t.test("unapplied legacy constraint keeps flag false and is not invented as product fact",async()=>{
   const r=await response({filters:{category_ids:["TV0108"],specs:[{attribute_query:"nothingxyz",op:"eq",value:1}]}});
   if(r.status!=="ok")assert.fail();assert.equal(r.meta.all_requested_filters_applied,false);assert.ok(r.unapplied_filters.length);
  });
  await t.test("ambiguous typed filter maps clarification",async()=>{
   const r=await response({filters:{category_ids:["TV0108"],specs:[{attribute_query:"ขนาด",op:"eq",value:55}]}});assert.equal(r.status,"needs_clarification");
  });
  await t.test("typed conflict maps error contract",async()=>{
   const r=await response({filters:{category_ids:["TV0108"],price:{max:1000},specs:[{attribute_id:"AT0048",op:"gte",value:65},{attribute_id:"AT0048",op:"lte",value:42}]}});
   assert.equal(r.status,"constraint_conflict");
  });
  await t.test("zero totals vs exhausted page retain distinct public statuses",async()=>{
   const a=await response({exact:{article_id:199999}}),b=await response({page:20,limit:1});
   assert.equal(a.status,"no_matches");assert.equal(b.status,"ok");if(b.status!=="ok")assert.fail();assert.deepEqual(b.results,[]);assert.equal(b.meta.has_more,false);
  });
  await t.test("conflicting branch is explained while valid category still searches",async()=>{
   const r=await response({filters:{category_ids:["TV0108","AP0101"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]}});
   assert.equal(validateToolSearchResponse(r).valid,true);if(r.status!=="ok")assert.fail();
   assert.ok(r.results.every(p=>p.product.category_id==="TV0108"));assert.equal(r.meta.all_requested_filters_applied,false);
   assert.ok(r.unapplied_filters.some(f=>f.category_id==="AP0101"));
  });
  await t.test("exact identity is reported as applied and never substituted",async()=>{
   const r=await response({exact:{article_id:100000,model:"TV0108-001"},filters:{category_ids:["TV0108"],price:{max:0}}});
   if(r.status!=="ok")assert.fail();assert.deepEqual(r.results.map(p=>p.product.article_id),[100000]);
   assert.ok(r.applied_filters.some(f=>f.path==="/exact/article_id" && f.value===100000));
  });
  await t.test("mapping cannot silently accept mismatched totals",async()=>{
   const r=await search();r.search!.meta!.returned_count+=1;assert.throws(()=>mapCatalogSearchResponse(r,"counts"),CatalogDataError);
  });
  await t.test("missing hydrated fact fails closed instead of changing counts",async()=>{
   const r=await search();r.products.pop();assert.throws(()=>mapCatalogSearchResponse(r,"missing"),CatalogDataError);
  });
  const awaitless=await search();
  await t.test("invalid output request ID fails validation",async()=>assert.throws(()=>mapCatalogSearchResponse(awaitless,""),CatalogDataError));
  await t.test("nonfinite price or missing mock notice raises data error and rolls back",async()=>{
   await db.exec("RESET ROLE;UPDATE products SET price='NaN' WHERE id=100000;SET ROLE search_api");
   await assert.rejects(()=>repo.findDetails(100000),CatalogDataError);assert.equal(queries.at(-1)!.sql,"ROLLBACK");
   await db.exec("RESET ROLE;UPDATE products SET price=20000,mock_notice='' WHERE id=100000;SET ROLE search_api");
   await assert.rejects(()=>repo.findDetails(100000),CatalogDataError);
   await db.exec("RESET ROLE;UPDATE products SET mock_notice='ข้อมูลจำลอง' WHERE id=100000;SET ROLE search_api");
  });
  await t.test("all successful/failed checkouts are released",async()=>{assert.equal(releases.length,checkedOut);assert.ok(releases.every(d=>!d));});
 }finally{await db.close();await rm(directory,{recursive:true,force:true});}
});

for(const [raw,expected] of [["0",0],["0.00",0],["55.25",55.25],["1e2",100],["NaN",null],["Infinity",null],["-Infinity",null],[null,null],["9007199254740993",null],["1e-9999",null],["0e-9999",0]] as const)
 test("decimal facts: "+raw,()=>assert.equal(decimalFact(raw),expected));

test("rollback failure discards client and preserves original error",async()=>{
 const original=Object.assign(new Error("private database error"),{code:"57014"});let released:unknown;
 const client={async query(sql:string){if(sql.startsWith("BEGIN"))return {rows:[]};if(sql==="ROLLBACK")throw new Error("lost socket");throw original;},release(value:unknown){released=value;}};
 const repo=createPostgresCatalogRepository({async connect(){return client;}} as unknown as CatalogPool);
 await assert.rejects(()=>repo.search({filters:{category_ids:["TV0108"]}}),e=>e===original);assert.equal(released,true);
});
test("connection failures stay errors, never empty products",async()=>{
 const error=Object.assign(new Error("connection refused"),{code:"ECONNREFUSED"});
 const repo=createPostgresCatalogRepository({async connect(){throw error;}} as unknown as CatalogPool);
 await assert.rejects(()=>repo.search({filters:{category_ids:["TV0108"]}}),e=>e===error);
});

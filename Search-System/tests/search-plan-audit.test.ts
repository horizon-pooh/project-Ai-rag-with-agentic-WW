import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve,join} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {vector} from "@electric-sql/pglite-pgvector";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import {pgcrypto} from "@electric-sql/pglite/contrib/pgcrypto";
import {Client,type ClientBase,type PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
const native=process.env.SEARCH_PLAN_TEST_MODE==="native";
async function fixture():Promise<PGlite>{
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.SEARCH_PLAN_TEST_HOST;if(!host?.startsWith("/private/tmp/"))throw new Error("Require private test socket");
 const c=new Client({host,port:Number(process.env.SEARCH_PLAN_TEST_PORT??55446),database:"catalog_plan_test_c26",user:"catalog_test_admin"});await c.connect();
 const i=(await c.query("SELECT current_database() db,current_user u,current_setting('server_version') v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) su")).rows[0];
 if(i.db!=="catalog_plan_test_c26" || i.u!=="catalog_test_admin" || !i.su || !/^18\.6(?:\D|$)/.test(i.v)){await c.end();throw new Error("Wrong test database");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}

import {inspectSearchPlans} from "../src/performance/search-plan-audit.js";
test("plan audit ใช้บัญชีอ่านและไม่แก้สินค้า",async t=>{
 const db=await fixture(),dir=await mkdtemp(join(tmpdir(),"search-plan-"));
 const client={async query(q:string,params?:unknown[]){const r=params?.length?await db.query(q,params):(await db.exec(q)).at(-1);return {rows:r?.rows??[]};}} as unknown as ClientBase;
 try{
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const f of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])await db.exec(await readFile(resolve("tests/fixtures",f),"utf8"));
  for(const f of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql","0008_semantic_category_resolution.sql","0009_product_hybrid_search.sql","0010_hybrid_alternatives.sql"])await copyFile(resolve("migrations",f),join(dir,f));
  await applyMigrations(client,dir,"up");
  await t.test("ปฏิเสธ input SQL ก่อนเริ่ม transaction",async()=>assert.rejects(inspectSearchPlans(client,{categoryId:"AP0101;DROP TABLE products",articleId:100000,attributeId:"AT0099"})));
  await t.test("ไม่วัดด้วย owner",async()=>assert.rejects(inspectSearchPlans(client)));
  await db.exec("SET ROLE search_api");
  let report;try{report=await inspectSearchPlans(client,{categoryId:"TV0108",articleId:100000,attributeId:"AT0048"});}finally{await db.exec("RESET ROLE");}
  await t.test("published และ private grants ตรงขอบเขต",()=>{assert.equal(report.visibility.drafts,0);assert.equal(report.write_probes_denied,true);assert.ok(report.private_tables.every(x=>x.denied));assert.ok(report.draft_children.every(x=>x.visible===0));});
  await t.test("หกคำสั่งอ่านได้ plan โดยไม่แสร้งว่ารู้ nested plan",()=>{assert.equal(report.plans.length,6);assert.ok(report.plans.every(x=>x.status==="ok"));assert.equal(report.plans[0]!.visibility,"function_wrapper_only");});
  await t.test(native?"native timeout ยกเลิกแล้ว connection ยังใช้ได้":"PGlite ไม่ยืนยัน timeout แทน native",()=>{assert.equal(report.statement_timeout.recovered,true);assert.equal(report.statement_timeout.code,native?"57014":"");assert.equal(report.status,native?"passed":"review_required");});
  await t.test("ไม่มีสินค้าเปลี่ยนหลัง probes",async()=>assert.equal((await db.query<{n:number}>("SELECT count(*)::int n FROM products")).rows[0]!.n,16));
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

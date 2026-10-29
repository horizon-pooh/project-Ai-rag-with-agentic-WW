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
import {registerEmbeddingModel} from "../src/repositories/embedding-backfill.repository.js";
import {embeddingConfigurationHash,DEFAULT_EMBEDDING_BOUNDS} from "../src/config/embedding.config.js";
import {readProductHybridConfig,type ProductHybridConfig} from "../src/config/product-hybrid.config.js";
import {createProductHybridEngine} from "../src/services/product-hybrid.service.js";
import {createCategorySemanticEngine} from "../src/services/category-semantic.service.js";
import {EmbeddingError,type EmbeddingProfile} from "../src/embeddings/embedding.types.js";
import type {QueryEmbeddingProvider} from "../src/services/category-semantic.service.js";
import type {KeywordSearchEnvelope} from "../src/types/keyword-search.types.js";
import type {ProductSemanticState} from "../src/types/product-hybrid.types.js";
import {parseToolSearchRequest} from "../src/validation/tool-search.validation.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import {mapCatalogSearchResponse} from "../src/validation/catalog-response.mapper.js";
import type {CatalogPool} from "../src/types/catalog-read.types.js";
import {buildApp} from "../src/app.js";
const profile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};
const hash=embeddingConfigurationHash(profile),sha="b".repeat(64);
const config=():ProductHybridConfig=>({profile:structuredClone(profile),bounds:{...DEFAULT_EMBEDDING_BOUNDS},baseUrl:"http://127.0.0.1:18081/v1",apiKey:undefined,modelId:"EM0001",artifactSha256:sha,configurationHash:hash});
function provider(){const p:QueryEmbeddingProvider & {calls:number;texts:string[]}={calls:0,texts:[],async embed(input){p.calls++;p.texts.push(...input.texts);return {provider:"llama_cpp",model:profile.model,dimensions:3,configurationHash:hash,purpose:"query",vectors:[[1,0,0]],usage:{promptTokens:4,totalTokens:4},attempts:1,tokenCounts:[4]};}};return p;}
const request=(patch:Record<string,unknown>={})=>{
 const p=parseToolSearchRequest({version:"1",query:"ทีวี",semantic_query:"ดูหนังในบ้าน",filters:{category_ids:["TV0108"],brand_queries:["LG"],feature_ids_all:["FT0074"],price:{max:25000},specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}]},limit:100,...patch});
 if(!p.valid)throw new Error("Invalid fixture request");return p.value;
};
const native=process.env.PRODUCT_HYBRID_TEST_MODE==="native";
async function fixture():Promise<PGlite>{
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.PRODUCT_HYBRID_TEST_HOST;if(!host?.startsWith("/private/tmp/"))throw new Error("Require private disposable test socket");
 const c=new Client({host,port:Number(process.env.PRODUCT_HYBRID_TEST_PORT??55442),database:"catalog_product_test_c22",user:"catalog_test_admin"});await c.connect();
 const r=(await c.query("SELECT current_database() db,current_user u,current_setting('server_version') v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) su")).rows[0];
 if(r.db!=="catalog_product_test_c22" || r.u!=="catalog_test_admin" || !r.su || !/^18\.6(?:\D|$)/.test(r.v)){await c.end();throw new Error("Wrong test target");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}
test("รวมอันดับใน SQL ภายใต้ตัวกรองเดียวกันและคงสิทธิ์อ่าน",async t=>{
 const db=await fixture(),dir=await mkdtemp(join(tmpdir(),"product-hybrid-"));let connections=0,inTransaction=false;
 const client={async query(q:string|{text:string;values?:unknown[]},values?:unknown[]){const sql=typeof q==="string"?q:q.text,args=values??(typeof q==="object"?q.values:undefined);const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);
  if(/^BEGIN/.test(sql))inTransaction=true;if(/^(COMMIT|ROLLBACK)/.test(sql))inTransaction=false;return {rows:r?.rows??[],rowCount:r?.affectedRows??0};}} as unknown as ClientBase;
 const dbc=client as unknown as PoolClient;
 const pool={async connect(){connections++;let released=false;return {query:client.query.bind(client),release(){if(!released){connections--;released=true;}}};}} as unknown as CatalogPool;
 const reader=async<T>(f:()=>Promise<T>)=>{await db.exec("SET ROLE search_api");try{return await f();}finally{await db.exec("RESET ROLE");}};
 const keyword=async(r=request(),scope=["TV0108"])=>((await db.query<{r:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb) r",[scope,JSON.stringify(r)])).rows[0]!.r);
 const hybrid=async(r=request(),scope=["TV0108"],v="[1,0,0]",digest=sha)=>((await db.query<{r:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_hybrid_v1($1::text[],$2::jsonb,$3::extensions.vector,$4,$5,$6) r",[scope,JSON.stringify(r),v,"EM0001",hash,digest])).rows[0]!.r);
 const state=async(scope=["TV0108"])=>((await db.query<{s:ProductSemanticState}>("SELECT catalog_search.product_semantic_state_v1($1::text[],$2,$3,$4) s",[scope,"EM0001",hash,sha])).rows[0]!.s);
 const seed=async()=>{
  await db.query(`INSERT INTO product_embeddings(product_id,model_id,embedding,content_hash,template_version,status,embedded_at)
   SELECT d.product_id,'EM0001',CASE WHEN d.product_id=100000 THEN '[0,1,0]' WHEN d.product_id=100005 THEN '[0.6,0.8,0]' ELSE '[1,0,0]' END::extensions.vector,
    d.content_hash,d.template_version,'ready',now() FROM product_search_documents d
   ON CONFLICT(product_id,model_id) DO UPDATE SET embedding=excluded.embedding,content_hash=excluded.content_hash,template_version=excluded.template_version,status='ready',embedded_at=now(),error_message=null`);
 };
 try {
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const f of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])await db.exec(await readFile(resolve("tests/fixtures",f),"utf8"));
  for(const f of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql","0008_semantic_category_resolution.sql","0009_product_hybrid_search.sql"])await copyFile(resolve("migrations",f),join(dir,f));
  await t.test("ติดตั้งเก้าขั้นและรันซ้ำโดยไม่สร้างข้อมูลเพิ่ม",async()=>{assert.equal((await applyMigrations(client,dir,"up")).length,9);assert.deepEqual(await applyMigrations(client,dir,"up"),[]);});
  // service ใช้รุ่นปัจจุบัน ส่วนตัวอย่าง SQL-v1 ยังใช้เทียบพฤติกรรมเดิม
  await copyFile(resolve("migrations/0010_hybrid_alternatives.sql"),join(dir,"0010_hybrid_alternatives.sql"));await applyMigrations(client,dir,"up");
  await t.test("โมเดลไม่พร้อมใช้ keyword แทน",async()=>reader(async()=>{const r=await hybrid();assert.equal(r.semantic_available,false);assert.deepEqual(r.results.map(x=>x.article_id),(await keyword()).results.map(x=>x.article_id));assert.ok(r.warnings.some(w=>w.code==="product_hybrid_model_unavailable"));}));
  await db.exec("BEGIN");await registerEmbeddingModel(dbc,"EM0001",profile,sha);await db.exec("COMMIT");
  await t.test("เวกเตอร์ไม่ครบไม่เรียกโมเดลและไม่รวมอันดับบางส่วน",async()=>reader(async()=>{const p=provider(),engine=createProductHybridEngine(config(),p),r=request(),s=await engine.readiness(dbc,["TV0108"],r);assert.equal(s.status,"coverage_incomplete");const prepared=await engine.prepare(["TV0108"],r,s);assert.equal(p.calls,0);const out=await engine.search(dbc,["TV0108"],r,prepared);assert.equal(out.semantic_available,false);assert.ok(out.warnings.some(w=>w.code==="product_hybrid_coverage_incomplete"));}));
  await seed();await db.exec("UPDATE products SET name_th='ทีวีทดสอบ',name_en='TV fixture' WHERE id IN(100000,100005,100006)");
  await t.test("นับเฉพาะ published ในหมวดที่เลือก",async()=>reader(async()=>{const s=await state();assert.equal(s.status,"ready");assert.equal(s.total,11);assert.equal(s.fresh,11);assert.equal((await state(["TV0109"])).total,1);}));
  await t.test("scope ที่ไม่มีสินค้าหรือรหัสผิดไม่สร้างผลลวง",async()=>reader(async()=>{assert.equal((await state(["FO0101"])).status,"empty_scope");await assert.rejects(()=>state(["ZZ9999"]));await assert.rejects(()=>state(["TV0100"]));}));
  await t.test("RRF รวมสองอันดับด้วย k60 และใช้ Article เมื่อตรงกัน",async()=>reader(async()=>{
   const r=await hybrid();assert.equal(r.engine_version,"hybrid-v1");assert.equal(r.semantic_available,true);assert.equal(r.meta!.total_matches,3);
   const strict=r.results.filter(x=>x.match_type==="matches_applied_filters");assert.deepEqual(strict.map(x=>x.article_id),[100000,100006,100005]);
   assert.equal(strict[0]!.keyword_rank,1);assert.equal(strict[0]!.vector_rank,3);assert.equal(strict[1]!.keyword_rank,3);assert.equal(strict[1]!.vector_rank,1);
   assert.ok(Math.abs(strict[0]!.rrf_score!-(1/61+1/63))<1e-12);assert.ok(Math.abs(strict[2]!.rrf_score!-2/62)<1e-12);
   assert.ok(!r.warnings.some(w=>w.code==="semantic_unavailable"));assert.ok(r.warnings.some(w=>w.code==="product_hybrid_rrf"));
  }));
  await t.test("กรองแบรนด์ ราคา สเปก ฟีเจอร์ ก่อนสร้างอันดับทั้งสองทาง",async()=>reader(async()=>{const r=await hybrid();const strict=r.results.filter(x=>x.match_type==="matches_applied_filters");assert.deepEqual(new Set(strict.map(x=>x.article_id)),new Set([100000,100005,100006]));const expensive=r.results.find(x=>x.article_id===100001)!;assert.equal(expensive.match_type,"alternative");assert.equal(expensive.vector_rank,null);assert.equal(expensive.rrf_score,null);assert.ok(expensive.unmet_filters.some(x=>x.path==="/filters/price/max"));}));
  await t.test("สินค้านอกหมวดและ draft ไม่ได้อันดับแม้เวกเตอร์ใกล้ที่สุด",async()=>reader(async()=>{const r=await hybrid();assert.ok(!r.results.some(x=>[100011,106045,106046].includes(x.article_id)));const rows=(await db.query<{article_id:number}>("SELECT * FROM catalog_search.product_vector_scores_v1($1::integer[],$2::text[],$3::extensions.vector,$4,$5,$6)",[[100000,100011,106045],["TV0108"],"[1,0,0]","EM0001",hash,sha])).rows;assert.deepEqual(rows.map(x=>x.article_id),[100000]);}));
  await t.test("ไม่มีคำ keyword ใช้อันดับเวกเตอร์ทางเดียว",async()=>reader(async()=>{const r=await hybrid(request({query:""}));assert.deepEqual(r.results.slice(0,3).map(x=>x.article_id),[100006,100005,100000]);assert.equal(r.results[0]!.keyword_rank,null);assert.ok(Math.abs(r.results[0]!.rrf_score!-1/61)<1e-12);}));
  await t.test("ผลตรงอยู่ก่อน alternatives และ alternatives ยังเรียงแบบเดิม",async()=>reader(async()=>{const a=await hybrid(),b=await keyword();assert.deepEqual(a.results.filter(x=>x.match_type==="alternative").map(x=>x.article_id),b.results.filter(x=>x.match_type==="alternative").map(x=>x.article_id));assert.ok(a.results.slice(0,3).every(x=>x.match_type==="matches_applied_filters"));}));
  await t.test("รวมอันดับก่อนแบ่งหน้า ไม่มี Article ซ้ำ",async()=>reader(async()=>{const full=await hybrid();const pages=[];for(let page=1;page<=6;page++){const r=await hybrid(request({page,limit:2}));assert.equal(r.meta!.total_matches,3);pages.push(...r.results.map(x=>x.article_id));}assert.deepEqual(pages,full.results.map(x=>x.article_id));assert.equal(new Set(pages).size,pages.length);}));
  await t.test("หน้าเกินผลเป็น ok ว่าง พร้อมยอดรวมเดิม",async()=>reader(async()=>{const r=await hybrid(request({page:100,limit:2}));assert.equal(r.status,"ok");assert.equal(r.results.length,0);assert.equal(r.meta!.has_more,false);assert.equal(r.semantic_available,true);}));
  for(const sort of ["price_asc","price_desc","id"])await t.test("การเรียง "+sort+" ใช้ทางเดิม",async()=>reader(async()=>{const r=request({sort}),a=await hybrid(r),b=await keyword(r);assert.equal(a.semantic_available,false);assert.deepEqual(a.results.map(x=>x.article_id),b.results.map(x=>x.article_id));assert.equal(createProductHybridEngine(config(),provider()).requested(r),false);}));
  await t.test("ไม่มีผลตรง ไม่อ้างว่า alternatives ผ่าน hybrid แล้ว",async()=>reader(async()=>{const a=await hybrid(request({query:"คำที่ไม่มีในข้อมูล"})),b=await keyword(request({query:"คำที่ไม่มีในข้อมูล"}));assert.equal(a.meta!.total_matches,0);assert.equal(a.semantic_available,false);assert.ok(a.warnings.some(w=>w.code==="product_hybrid_no_strict_candidates"));assert.deepEqual(a.results.map(x=>x.article_id),b.results.map(x=>x.article_id));}));
  await t.test("ค่า false หน่วยแปลง และค่าไม่มีข้อมูลยังตรง keyword",async()=>reader(async()=>{const r=request({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:139.7,unit:"cm"},{attribute_id:"AT0093",op:"eq",value:false}]}});const a=await hybrid(r),b=await keyword(r);assert.equal(a.meta!.total_matches,b.meta!.total_matches);for(const hit of a.results){const old=b.results.find(x=>x.article_id===hit.article_id)!;assert.deepEqual(hit.unmet_filters,old.unmet_filters);assert.deepEqual(hit.unknown_filters,old.unknown_filters);}assert.ok(a.results.find(x=>x.article_id===100008)!.unknown_filters.some(x=>x.actual_value===null));}));
  await t.test("หลายหมวดรวมและตัดซ้ำก่อนแบ่งหน้า",async()=>reader(async()=>{const r=request({filters:{category_ids:["TV0108","TV0109"]}}),out=await hybrid(r,["TV0108","TV0109"]);assert.equal(out.meta!.total_matches,12);assert.ok(out.results.some(x=>x.category_id==="TV0109"));assert.equal(new Set(out.results.map(x=>x.article_id)).size,12);}));
  await t.test("exact Article และรุ่น ไม่แทนด้วยสินค้าอื่น",async()=>reader(async()=>{for(const exact of [{article_id:100000},{model:"TV0108-001"}]){const r=request({exact}),out=await hybrid(r);assert.equal(out.semantic_available,false);assert.deepEqual(out.results.map(x=>x.article_id),[100000]);assert.equal(createProductHybridEngine(config(),provider()).requested(r),false);}}));
  await t.test("ราคาเป็นศูนย์ยังเป็นเงื่อนไขจริง",async()=>reader(async()=>{
   const out=await hybrid(request({filters:{category_ids:["TV0108"],price:{max:0}}}));assert.equal(out.meta!.total_matches,1);assert.equal(out.results[0]!.article_id,100007);assert.equal(out.semantic_available,true);
  }));
  await t.test("ตัวกรองยังเลือกไม่ได้จะไม่เรียกโมเดล",async()=>reader(async()=>{
   const p=provider(),e=createProductHybridEngine(config(),p),r=request({filters:{category_ids:["TV0108"],brand_queries:["UNKNOWN-FIXTURE-BRAND"]}}),s=await e.readiness(dbc,["TV0108"],r);
   assert.equal(s.status,"filters_unresolved");const out=await e.search(dbc,["TV0108"],r,await e.prepare(["TV0108"],r,s));assert.equal(out.status,"needs_clarification");assert.equal(p.calls,0);
  }));
  await t.test("รุ่นที่มีจริงแม้ไม่เข้ารูปแบบรหัสก็ไม่เรียกโมเดล",async()=>{
   await db.exec("UPDATE products SET model='KNOWN-MODEL-A' WHERE id=100000");
   try{await reader(async()=>{const p=provider(),e=createProductHybridEngine(config(),p),r=request({query:"KNOWN-MODEL-A"}),s=await e.readiness(dbc,["TV0108"],r);assert.equal(s.status,"identity_lock");const out=await e.search(dbc,["TV0108"],r,await e.prepare(["TV0108"],r,s));assert.deepEqual(out.results.map(x=>x.article_id),[100000]);assert.equal(p.calls,0);});}
   finally{await db.exec("UPDATE products SET model='TV0108-001' WHERE id=100000");}
  });
  await t.test("query ที่เป็น Article หรือรุ่นที่มีจริงใช้ identity lock",async()=>reader(async()=>{for(const query of ["100000","TV0108-001"]){const out=await hybrid(request({query}));assert.deepEqual(out.results.map(x=>x.article_id),[100000]);assert.equal(out.semantic_available,false);}}));
  await t.test("เงื่อนไขขัดกันและสเปกกำกวมไม่ถูกเวกเตอร์กลบ",async()=>reader(async()=>{const conflict=request({filters:{category_ids:["TV0108"],price:{max:100},specs:[{attribute_id:"AT0048",op:"gte",value:80},{attribute_id:"AT0048",op:"lte",value:40}]}});assert.equal((await hybrid(conflict)).status,"constraint_conflict");const unknown=request({filters:{category_ids:["TV0108"],specs:[{attribute_query:"ขนาด",op:"eq",value:55}]}});assert.equal((await hybrid(unknown)).status,"needs_clarification");}));
  await t.test("เวกเตอร์ผิดมิติ ศูนย์ หรือ norm ผิดถูกปฏิเสธ",async()=>reader(async()=>{for(const v of ["[1,0]","[0,0,0]","[1,1,0]"])await assert.rejects(()=>hybrid(request(),["TV0108"],v));}));
  await t.test("SHA ไม่ตรงไม่ใช้เวกเตอร์ของโมเดลอื่น",async()=>reader(async()=>{const r=await hybrid(request(),["TV0108"],"[1,0,0]","a".repeat(64));assert.equal(r.semantic_available,false);assert.ok(r.warnings.some(w=>w.code==="product_hybrid_model_unavailable"));}));
  await t.test("ผู้อ่านเรียกคะแนนได้แต่เปิดตารางเวกเตอร์ไม่ได้",async()=>reader(async()=>{for(const table of ["product_embeddings","embedding_models"])await assert.rejects(()=>db.exec("SELECT * FROM "+table),e=>typeof e==="object" && e!==null && "code" in e && e.code==="42501");}));
  await t.test("anonymous เรียกฟังก์ชันใหม่ไม่ได้",async()=>{await db.exec("SET ROLE anon");try{await assert.rejects(()=>hybrid(),e=>typeof e==="object" && e!==null && "code" in e && e.code==="42501");await assert.rejects(()=>state());}finally{await db.exec("RESET ROLE");}});
  await t.test("ต้นทางเปลี่ยนหลังสร้าง query vector จะกลับไป keyword",async()=>{
   const p=provider(),engine=createProductHybridEngine(config(),p),r=request();const prepared=await reader(async()=>engine.prepare(["TV0108"],r,await engine.readiness(dbc,["TV0108"],r)));
   await db.exec("UPDATE product_search_documents SET template_version='changed-after-request' WHERE product_id=100000");
   const out=await reader(()=>engine.search(dbc,["TV0108"],r,prepared));assert.equal(out.semantic_available,false);assert.ok(out.warnings.some(w=>w.code==="product_hybrid_coverage_incomplete"));await seed();
  });
  await t.test("เอกสารสินค้าขาดทำให้ coverage ไม่ครบ",async()=>{await db.exec("INSERT INTO products(id,category_id,brand_id,model,name_th,price,status) VALUES(100099,'TV0108','BR0002','TV0108-099','ไม่มีเอกสาร',100,'published')");assert.equal((await reader(()=>state())).status,"coverage_incomplete");await db.exec("DELETE FROM products WHERE id=100099");});
  await t.test("โมเดลถูกปิดหลังเตรียมคำค้นไม่ใช้คะแนนเก่า",async()=>{
   const engine=createProductHybridEngine(config(),provider()),r=request(),prepared=await reader(async()=>engine.prepare(["TV0108"],r,await engine.readiness(dbc,["TV0108"],r)));
   await db.exec("UPDATE embedding_models SET is_active=false WHERE id='EM0001'");const out=await reader(()=>engine.search(dbc,["TV0108"],r,prepared));assert.equal(out.semantic_available,false);await db.exec("UPDATE embedding_models SET is_active=true WHERE id='EM0001'");
  });
  await t.test("scope เปลี่ยนไม่ใช้เวกเตอร์ที่เตรียมจากขอบเขตเดิม",async()=>reader(async()=>{const engine=createProductHybridEngine(config(),provider()),r=request(),prepared=await engine.prepare(["TV0108"],r,await engine.readiness(dbc,["TV0108"],r));const other=request({filters:{category_ids:["TV0109"]}}),out=await engine.search(dbc,["TV0109"],other,prepared);assert.equal(out.semantic_available,false);assert.ok(out.warnings.some(w=>w.code==="product_hybrid_scope_changed"));}));
  for(const code of ["network_error","timeout","input_too_long","model_mismatch"] as const)await t.test("provider "+code+" กลับไป keyword พร้อมเหตุผล",async()=>reader(async()=>{const p=provider();p.embed=async()=>{throw new EmbeddingError(code);};const engine=createProductHybridEngine(config(),p),r=request(),out=await engine.search(dbc,["TV0108"],r,await engine.prepare(["TV0108"],r,await engine.readiness(dbc,["TV0108"],r)));assert.equal(out.semantic_available,false);assert.ok(out.warnings.some(w=>w.code==="product_hybrid_"+code));}));
  await t.test("provider ส่ง hash หรือเวกเตอร์ผิดไม่ถูกนำไป SQL",async()=>reader(async()=>{const p=provider(),old=p.embed;p.embed=async input=>({...await old(input),configurationHash:"a".repeat(64)});const e=createProductHybridEngine(config(),p),r=request(),prepared=await e.prepare(["TV0108"],r,await e.readiness(dbc,["TV0108"],r));assert.equal(prepared.vector,null);assert.equal(prepared.warning?.code,"product_hybrid_invalid_response");}));
  await t.test("ไม่ค้าง connection ระหว่างโมเดลและใช้ semantic_query ก่อน query",async()=>reader(async()=>{const p=provider(),old=p.embed;p.embed=async input=>{assert.equal(inTransaction,false);assert.equal(connections,0);return old(input);};const snap=await createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),p)}).search(request());assert.equal(snap.search!.semantic_available,true);assert.deepEqual(p.texts,["ดูหนังในบ้าน"]);const mapped=mapCatalogSearchResponse(snap,"hybrid-fixture");assert.ok(mapped.status==="ok");if(mapped.status==="ok"){assert.equal(mapped.semantic_available,true);assert.ok(mapped.results[0]!.product.mock_notice);assert.ok(!JSON.stringify(mapped).includes('"rrf_score"'));}}));
  await t.test("ไม่มี semantic_query ใช้ query เป็นคำสร้างเวกเตอร์",async()=>reader(async()=>{const p=provider(),r={...request()};delete r.semantic_query;const snap=await createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),p)}).search(r);assert.equal(snap.search!.semantic_available,true);assert.deepEqual(p.texts,["ทีวี"]);}));
  await t.test("SQL ผิดพลาดไม่คืนเป็นสินค้าไม่พบ",async()=>{await db.exec("ALTER FUNCTION catalog_search.product_semantic_state_v1(text[],text,text,text) RENAME TO fixture_hidden_state");try{await reader(async()=>{await assert.rejects(()=>createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),provider())}).search(request()));});}finally{await db.exec("ALTER FUNCTION catalog_search.fixture_hidden_state(text[],text,text,text) RENAME TO product_semantic_state_v1");}});
  await t.test("HTTP ตรวจ token และ JSON ก่อนโมเดล และส่งผล hybrid รุ่นเดิม",async()=>reader(async()=>{
   const p=provider(),catalog=createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),p)}),token="x".repeat(43),app=buildApp({database:null,api:{token},catalog,logger:false});
   try{const a=await app.inject({method:"POST",url:"/api/search",payload:request()});assert.equal(a.statusCode,401);assert.equal(p.calls,0);
    const b=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:{...request(),query_vector:[1,0,0]}});assert.equal(b.statusCode,400);assert.equal(p.calls,0);
    const c=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:request()});assert.equal(c.statusCode,200);assert.equal(c.json().semantic_available,true);assert.equal(c.json().version,"1");assert.equal(p.calls,1);
   }finally{await app.close();}
  }));
  await t.test("หมวด semantic และสินค้า hybrid ทำงานต่อกันโดยไม่แจ้งว่า keyword อย่างเดียว",async()=>{
   await db.query("INSERT INTO category_embeddings(category_id,model_id,embedding,document_hash,template_version,status,embedded_at) SELECT category_id,'EM0001','[1,0,0]'::extensions.vector,source_hash,template_version,'ready',now() FROM category_search_documents");
   const category=createCategorySemanticEngine({...config(),policy:{autoSelect:true,minScore:0.8,minGap:0.1,calibrationId:"fixture-only"}},provider());
   const p=provider(),req=request({filters:{category_query:"ทีวี OLED สำหรับดูหนัง"}});
   const snap=await reader(()=>createPostgresCatalogRepository(pool,{categorySemantic:category,productHybrid:createProductHybridEngine(config(),p)}).search(req));const out=mapCatalogSearchResponse(snap,"combined-fixture");assert.ok(out.status==="ok");if(out.status==="ok"){assert.equal(out.semantic_available,true);assert.ok(out.warnings.some(w=>w.code==="category_semantic_scope_selected"));assert.ok(!out.warnings.some(w=>w.message.includes("product ranking remains keyword-only")));}
  });
  await t.test("rollback ลบเฉพาะฟังก์ชันใหม่ คงข้อมูลและ keyword",async()=>{const n=(await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings")).rows[0]!.n;await applyMigrations(client,dir,"down");await applyMigrations(client,dir,"down");assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings")).rows[0]!.n,n);assert.equal((await reader(()=>keyword())).engine_version,"keyword-v2");assert.deepEqual(await applyMigrations(client,dir,"up"),["0009_product_hybrid_search","0010_hybrid_alternatives"]);});
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});
const env=()=>({SEARCH_PRODUCT_HYBRID_ENABLED:"true",SEARCH_PRODUCT_HYBRID_MODEL_ID:"EM0001",EMBEDDING_ARTIFACT_SHA256:sha,EMBEDDING_PROVIDER:"llama_cpp",EMBEDDING_MODEL:profile.model,
 EMBEDDING_DIMENSIONS:"3",EMBEDDING_CONTEXT_TOKENS:"2048",EMBEDDING_CONFIG_VERSION:profile.version,EMBEDDING_RUNTIME_VERSION:profile.runtimeVersion,EMBEDDING_ARTIFACT:profile.artifact,
 EMBEDDING_DISTANCE_METRIC:"cosine",EMBEDDING_POOLING:"last",EMBEDDING_NORMALIZATION:"server_l2",EMBEDDING_BASE_URL:"http://127.0.0.1:18081/v1"});
test("ค่าเริ่มต้นไม่เปิด hybrid และเปิดด้วย profile ที่ตรวจได้",()=>{assert.equal(readProductHybridConfig({}),null);assert.equal(readProductHybridConfig({...env(),SEARCH_PRODUCT_HYBRID_ENABLED:"false"}),null);assert.equal(readProductHybridConfig(env())!.configurationHash,hash);});
for(const patch of [{SEARCH_PRODUCT_HYBRID_ENABLED:"yes"},{SEARCH_PRODUCT_HYBRID_MODEL_ID:"EM1"},{EMBEDDING_ARTIFACT_SHA256:"bad"},{EMBEDDING_DIMENSIONS:"1024"}])test("ตรวจค่าตั้ง hybrid "+Object.keys(patch)[0],()=>{if('EMBEDDING_DIMENSIONS' in patch)assert.notEqual(readProductHybridConfig({...env(),...patch})!.configurationHash,hash);else assert.throws(()=>readProductHybridConfig({...env(),...patch}));});
test("ไม่สร้างเวกเตอร์เมื่อเป็น identity การเรียงราคา หรือไม่มีคำค้น",()=>{const e=createProductHybridEngine(config(),provider());for(const r of [request({query:"100000"}),request({query:"TV0108-001"}),request({sort:"price_asc"}),request({query:"",semantic_query:undefined,filters:{category_ids:["TV0108"]}})])assert.equal(e.requested(r),false);});

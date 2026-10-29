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
import type {ProductHybridConfig} from "../src/config/product-hybrid.config.js";
import {createProductHybridEngine} from "../src/services/product-hybrid.service.js";
import type {QueryEmbeddingProvider} from "../src/services/category-semantic.service.js";
import {EmbeddingError,type EmbeddingProfile} from "../src/embeddings/embedding.types.js";
import {parseToolSearchRequest,validateToolSearchResponse} from "../src/validation/tool-search.validation.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import {mapCatalogSearchResponse} from "../src/validation/catalog-response.mapper.js";
import type {CatalogPool} from "../src/types/catalog-read.types.js";
import type {KeywordSearchEnvelope} from "../src/types/keyword-search.types.js";
import {buildApp} from "../src/app.js";
const profile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};
const hash=embeddingConfigurationHash(profile),sha="b".repeat(64);
const config=():ProductHybridConfig=>({profile:structuredClone(profile),bounds:{...DEFAULT_EMBEDDING_BOUNDS},baseUrl:"http://127.0.0.1:18081/v1",apiKey:undefined,modelId:"EM0001",artifactSha256:sha,configurationHash:hash});
function provider(){const p:QueryEmbeddingProvider & {calls:number}={calls:0,async embed(){p.calls++;return {provider:"llama_cpp",model:profile.model,dimensions:3,configurationHash:hash,purpose:"query",vectors:[[1,0,0]],usage:{promptTokens:4,totalTokens:4},attempts:1,tokenCounts:[4]};}};return p;}
const request=(patch:Record<string,unknown>={})=>{
 const p=parseToolSearchRequest({version:"1",query:"ทีวี",semantic_query:"ดูหนังในบ้าน",filters:{category_ids:["TV0108"],brand_queries:["LG"],feature_ids_all:["FT0074"],price:{max:25000},specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}]},limit:100,...patch});
 if(!p.valid)throw new Error("Invalid fixture request");return p.value;
};
const native=process.env.HYBRID_ALTERNATIVES_TEST_MODE==="native";
async function fixture():Promise<PGlite>{
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.HYBRID_ALTERNATIVES_TEST_HOST;if(!host?.startsWith("/private/tmp/"))throw new Error("Require isolated Unix socket");
 const c=new Client({host,port:Number(process.env.HYBRID_ALTERNATIVES_TEST_PORT??55443),database:"catalog_alternatives_test_c23",user:"catalog_test_admin"});await c.connect();
 const r=(await c.query("SELECT current_database() db,current_user u,current_setting('server_version') v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) su")).rows[0];
 if(r.db!=="catalog_alternatives_test_c23" || r.u!=="catalog_test_admin" || !r.su || !/^18\.6(?:\D|$)/.test(r.v)){await c.end();throw new Error("Wrong test target");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}
test("hybrid alternatives คงข้อเท็จจริงและรวมอันดับก่อนแบ่งหน้า",async t=>{
 const db=await fixture(),dir=await mkdtemp(join(tmpdir(),"hybrid-alternatives-"));let connections=0,inTransaction=false;
 const client={async query(q:string|{text:string;values?:unknown[]},values?:unknown[]){const sql=typeof q==="string"?q:q.text,args=values??(typeof q==="object"?q.values:undefined);const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);if(/^BEGIN/.test(sql))inTransaction=true;if(/^(COMMIT|ROLLBACK)/.test(sql))inTransaction=false;return {rows:r?.rows??[],rowCount:r?.affectedRows??0};}} as unknown as ClientBase;
 const dbc=client as unknown as PoolClient;
 const pool={async connect(){connections++;let released=false;return {query:client.query.bind(client),release(){if(!released){connections--;released=true;}}};}} as unknown as CatalogPool;
 const reader=async<T>(f:()=>Promise<T>)=>{await db.exec("SET ROLE search_api");try{return await f();}finally{await db.exec("RESET ROLE");}};
 const keyword=async(r=request(),scope=["TV0108"])=>((await db.query<{r:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb) r",[scope,JSON.stringify(r)])).rows[0]!.r);
 const hybrid=async(r=request(),scope=["TV0108"],vec="[1,0,0]",digest=sha)=>((await db.query<{r:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_hybrid_v2($1::text[],$2::jsonb,$3::extensions.vector,$4,$5,$6) r",[scope,JSON.stringify(r),vec,"EM0001",hash,digest])).rows[0]!.r);
 const seed=async()=>{
  await db.query(`INSERT INTO product_embeddings(product_id,model_id,embedding,content_hash,template_version,status,embedded_at)
   SELECT d.product_id,'EM0001',CASE WHEN d.product_id=100006 THEN '[1,0,0]' WHEN d.product_id=100005 THEN '[0.6,0.8,0]' ELSE '[0,1,0]' END::extensions.vector,
    d.content_hash,d.template_version,'ready',now() FROM product_search_documents d
   ON CONFLICT(product_id,model_id) DO UPDATE SET embedding=excluded.embedding,content_hash=excluded.content_hash,template_version=excluded.template_version,status='ready',embedded_at=now(),error_message=null`);
 };
 try{
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const f of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])await db.exec(await readFile(resolve("tests/fixtures",f),"utf8"));
  for(const f of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql","0008_semantic_category_resolution.sql","0009_product_hybrid_search.sql","0010_hybrid_alternatives.sql"])await copyFile(resolve("migrations",f),join(dir,f));
  await t.test("ติดตั้งสิบขั้นและรันซ้ำได้",async()=>{assert.equal((await applyMigrations(client,dir,"up")).length,10);assert.deepEqual(await applyMigrations(client,dir,"up"),[]);});
  await t.test("โมเดลไม่พร้อมคืน keyword โดยไม่ประดิษฐ์อันดับ",async()=>reader(async()=>{const a=await hybrid(),b=await keyword();assert.equal(a.semantic_available,false);assert.deepEqual(a.results.map(x=>x.article_id),b.results.map(x=>x.article_id));}));
  await db.exec("BEGIN");await registerEmbeddingModel(dbc,"EM0001",profile,sha);await db.exec("COMMIT");
  await t.test("เวกเตอร์บางส่วนไม่ถูกใช้จัด alternatives",async()=>reader(async()=>{const a=await hybrid(request({query:"คำที่ไม่พบ"}));assert.equal(a.semantic_available,false);assert.ok(a.warnings.some(w=>w.code==="product_hybrid_coverage_incomplete"));}));
  await seed();await db.exec("UPDATE products SET name_th='ทีวีทดสอบ',name_en='TV fixture' WHERE id IN(100000,100005,100006)");
  await t.test("ผลตรงมาก่อนและเติม alternatives จนครบ limit",async()=>reader(async()=>{
   const a=await hybrid(request({limit:5}));assert.equal(a.engine_version,"hybrid-v2");assert.equal(a.semantic_available,true);assert.equal(a.meta!.total_matches,3);assert.equal(a.meta!.returned_count,5);
   assert.deepEqual(a.results.slice(0,3).map(x=>x.article_id),[100000,100006,100005]);assert.ok(a.results.slice(3).every(x=>x.match_type==="alternative" && x.unmet_filters.length+x.unknown_filters.length>0));
   assert.ok(a.results.every(x=>x.vector_rank!==null && x.rrf_score!>0));
  }));
  await t.test("strict RRF แยกอันดับจาก alternatives และคะแนนเสมอใช้ Article",async()=>reader(async()=>{
   const a=await hybrid();const strict=a.results.filter(x=>x.match_type==="matches_applied_filters");assert.equal(strict[0]!.keyword_rank,1);assert.equal(strict[0]!.vector_rank,3);assert.equal(strict[1]!.keyword_rank,3);assert.equal(strict[1]!.vector_rank,1);assert.equal(strict[0]!.rrf_score,strict[1]!.rrf_score);
   assert.ok(Math.abs(strict[0]!.rrf_score!-(1/61+1/63))<1e-12);
  }));
  await t.test("ไม่มีผลตรงก็จัด alternatives ด้วย RRF ได้",async()=>reader(async()=>{
   const r=request({query:"คำที่ไม่มีในข้อมูล"}),a=await hybrid(r),b=await keyword(r);assert.equal(a.meta!.total_matches,0);assert.equal(a.meta!.total_alternatives,11);assert.equal(a.semantic_available,true);assert.equal(a.status,"ok");
   assert.ok(a.results.every(x=>x.match_type==="alternative" && x.vector_rank!==null));assert.deepEqual(a.results.slice(0,3).map(x=>x.article_id),[100000,100006,100005]);assert.deepEqual(b.results.slice(0,3).map(x=>x.article_id),[100000,100005,100006]);
   assert.ok(a.results.every(x=>x.unmet_filters.some(d=>d.path==="/query")));assert.ok(!a.warnings.some(w=>w.code==="semantic_unavailable" || w.code==="product_hybrid_no_strict_candidates"));
  }));
  await t.test("รุ่น SQL-v1 เดิมยังคงพฤติกรรมเดิม",async()=>reader(async()=>{
   const r=request({query:"คำที่ไม่มีในข้อมูล"}),old=(await db.query<{r:KeywordSearchEnvelope}>("SELECT catalog_search.search_products_hybrid_v1($1::text[],$2::jsonb,$3::extensions.vector,$4,$5,$6) r",[["TV0108"],JSON.stringify(r),"[1,0,0]","EM0001",hash,sha])).rows[0]!.r;
   assert.equal(old.semantic_available,false);assert.equal((await hybrid(r)).semantic_available,true);
  }));
  await t.test("ผิดเงื่อนไขน้อยกว่ามาก่อนแม้ vector ใกล้น้อยกว่า",async()=>reader(async()=>{
   const a=await hybrid(request({query:"คำที่ไม่มีในข้อมูล"}));const top=a.results[0]!,far=a.results.find(x=>x.article_id===100001)!;assert.ok(top.difference_count<far.difference_count);assert.ok(a.results.indexOf(top)<a.results.indexOf(far));
  }));
  await t.test("ความใกล้ของค่าตัวเลขมาก่อนคะแนนเวกเตอร์",async()=>{
   await db.exec("UPDATE product_embeddings SET embedding='[1,0,0]' WHERE product_id=100001");
   try{await reader(async()=>{const a=await hybrid(request({filters:{category_ids:["TV0108"],price:{max:1000}}}));const cheap=a.results.find(x=>x.article_id===100002)!,expensive=a.results.find(x=>x.article_id===100001)!;assert.equal(cheap.difference_count,expensive.difference_count);assert.equal(cheap.unknown_count,expensive.unknown_count);assert.ok(cheap.numeric_distance<expensive.numeric_distance);assert.ok(a.results.indexOf(cheap)<a.results.indexOf(expensive));});}finally{await seed();}
  });
  await t.test("ค่าที่ทราบว่าต่างมาก่อนค่าที่ไม่ทราบ ไม่แทน null ด้วยศูนย์",async()=>{
   await db.exec("UPDATE product_embeddings SET embedding='[1,0,0]' WHERE product_id=100003");
   try{await reader(async()=>{const a=await hybrid(request({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]}}));const known=a.results.find(x=>x.article_id===100001)!,unknown=a.results.find(x=>x.article_id===100003)!;assert.equal(known.difference_count,1);assert.equal(unknown.difference_count,1);assert.ok(a.results.indexOf(known)<a.results.indexOf(unknown));assert.equal(unknown.unknown_filters[0]!.actual_value,null);});}finally{await seed();}
  });
  await t.test("เหตุผล หน่วย ค่า false และตำแหน่ง JSON ยังตรง keyword",async()=>reader(async()=>{
   const r=request({filters:{category_ids:["TV0108"],price:{max:25000},specs:[{attribute_id:"AT0048",op:"eq",value:139.7,unit:"cm"},{attribute_id:"AT0093",op:"eq",value:false}]}}),a=await hybrid(r),b=await keyword(r);
   assert.equal(a.meta!.total_matches,b.meta!.total_matches);for(const hit of a.results){const old=b.results.find(x=>x.article_id===hit.article_id)!;assert.equal(hit.match_type,old.match_type);assert.deepEqual(hit.unmet_filters,old.unmet_filters);assert.deepEqual(hit.unknown_filters,old.unknown_filters);}
  }));
  await t.test("ช่วงซ้ำคงทุก path แต่ไม่นับความผิดซ้ำเป็นหลายสเปก",async()=>reader(async()=>{
   const a=await hybrid(request({query:"คำที่ไม่มีในข้อมูล",filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0006",op:"gte",value:100},{attribute_id:"AT0006",op:"lte",value:100}]}})),hit=a.results.find(x=>x.article_id===100004)!;
   assert.equal(hit.difference_count,2);assert.equal(hit.unknown_count,1);assert.deepEqual(hit.unknown_filters.map(x=>x.path),["/filters/specs/0","/filters/specs/1"]);assert.ok(hit.unknown_filters.every(x=>x.actual_value===null));
  }));
  await t.test("ข้อมูลมีน้อยกว่า limit คืนเท่าที่มีและไม่ออกนอกหมวด",async()=>reader(async()=>{
   const a=await hybrid(request({limit:5,filters:{category_ids:["TV0109"],price:{max:1}}}),["TV0109"]);assert.equal(a.meta!.returned_count,1);assert.equal(a.meta!.total_matches,0);assert.equal(a.meta!.total_alternatives,1);assert.equal(a.meta!.has_more,false);assert.deepEqual(a.results.map(x=>x.article_id),[100011]);assert.equal(a.results[0]!.match_type,"alternative");
  }));
  await t.test("หลายหมวดรวมก่อนแบ่งหน้า ไม่มีผลซ้ำและไม่อ่าน draft",async()=>reader(async()=>{
   const r=request({filters:{category_ids:["TV0108","TV0109"],price:{max:1}}}),full=await hybrid(r,["TV0108","TV0109"]),pages=[];
   for(let page=1;page<=6;page++){const p=await hybrid({...r,page,limit:2},["TV0108","TV0109"]);pages.push(...p.results.map(x=>x.article_id));assert.equal(p.meta!.total_matches,full.meta!.total_matches);assert.equal(p.meta!.total_alternatives,full.meta!.total_alternatives);}
   assert.deepEqual(pages,full.results.map(x=>x.article_id));assert.equal(new Set(pages).size,12);assert.ok(!pages.includes(106045) && !pages.includes(106046));
  }));
  await t.test("หน้าเกินผลยังเป็น ok ไม่ใช่ no_matches",async()=>reader(async()=>{const a=await hybrid(request({query:"คำที่ไม่มีในข้อมูล",page:100,limit:2}));assert.equal(a.status,"ok");assert.equal(a.semantic_available,true);assert.equal(a.results.length,0);assert.equal(a.meta!.has_more,false);assert.equal(a.meta!.total_alternatives,11);}));
  await t.test("สลับความใกล้เวกเตอร์แล้วผลตรงยังอยู่หน้าทุก alternative",async()=>{
   await db.exec("UPDATE product_embeddings SET embedding=CASE WHEN product_id IN(100000,100005,100006) THEN '[0,1,0]' ELSE '[1,0,0]' END::extensions.vector");
   try{await reader(async()=>{const a=await hybrid(request({limit:5}));assert.ok(a.results.slice(0,3).every(x=>x.match_type==="matches_applied_filters"));assert.ok(a.results.slice(3).every(x=>x.match_type==="alternative"));});}finally{await seed();}
  });
  await t.test("ไม่มี keyword ใช้ vector ทางเดียวทั้งสองกลุ่ม",async()=>reader(async()=>{
   const a=await hybrid(request({query:"",filters:{category_ids:["TV0108"],price:{max:1}}}));assert.equal(a.semantic_available,true);assert.ok(a.results.every(x=>x.keyword_rank===null && x.vector_rank!==null));
   for(const hit of a.results)assert.ok(Math.abs(hit.rrf_score!-1/(60+hit.vector_rank!))<1e-12);
  }));
  await t.test("ราคาเป็นศูนย์ยังถูกกรองตามจริง",async()=>reader(async()=>{const a=await hybrid(request({filters:{category_ids:["TV0108"],price:{max:0}}}));assert.equal(a.meta!.total_matches,1);assert.equal(a.results[0]!.article_id,100007);assert.ok(a.results.filter(x=>x.article_id!==100007).every(x=>x.match_type==="alternative"));}));
  for(const sort of ["price_asc","price_desc","id"])await t.test("sort "+sort+" คงลำดับเดิมและไม่อ้างว่าใช้ vector",async()=>reader(async()=>{const r=request({sort}),a=await hybrid(r),b=await keyword(r);assert.equal(a.semantic_available,false);assert.deepEqual(a.results.map(x=>x.article_id),b.results.map(x=>x.article_id));}));
  await t.test("identity ไม่ถูกผ่อนเพื่อเติมรายการ",async()=>reader(async()=>{
   for(const exact of [{article_id:100000},{model:"TV0108-001"}]){const a=await hybrid(request({exact,filters:{category_ids:["TV0108"],price:{max:1}}}));assert.deepEqual(a.results.map(x=>x.article_id),[100000]);assert.equal(a.results[0]!.match_type,"alternative");assert.equal(a.semantic_available,false);}
   const unknown=await hybrid(request({query:"TV0108-999"}));assert.equal(unknown.status,"no_matches");assert.deepEqual(unknown.results,[]);
  }));
  await t.test("ข้อขัดแย้งหรือรหัสที่ไม่รู้จักไม่ถูก RRF กลบ",async()=>reader(async()=>{
   assert.equal((await hybrid(request({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"gte",value:80},{attribute_id:"AT0048",op:"lte",value:40}]}}))).status,"constraint_conflict");
   assert.equal((await hybrid(request({filters:{category_ids:["TV0108"],feature_ids_all:["FT9999"]}}))).status,"needs_clarification");
  }));
  await t.test("สเปกที่ใช้กรองไม่ได้ยังแสดง unapplied และไม่อ้างว่าผ่านครบ",async()=>reader(async()=>{
   const r=request({query:"คำที่ไม่มีในข้อมูล",filters:{category_ids:["TV0108"],specs:[{attribute_query:"ความกว้างบรรจุภัณฑ์",op:"gte",value:100}]}});
   const snapshot=await createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),provider())}).search(r),mapped=mapCatalogSearchResponse(snapshot,"unapplied-fixture");assert.equal(mapped.status,"ok");
   if(mapped.status==="ok"){assert.equal(mapped.meta.all_requested_filters_applied,false);assert.ok(mapped.unapplied_filters.some(x=>x.path==="/filters/specs/0"));assert.equal(validateToolSearchResponse(mapped).valid,true);}
  }));
  await t.test("เวกเตอร์ขาดแม้เป็น alternative ต้องกลับไป keyword ทั้งชุด",async()=>{
   await db.exec("UPDATE product_embeddings SET status='stale' WHERE product_id=100001");try{await reader(async()=>{const a=await hybrid(),b=await keyword();assert.equal(a.semantic_available,false);assert.deepEqual(a.results.map(x=>x.article_id),b.results.map(x=>x.article_id));assert.ok(a.warnings.some(w=>w.code==="product_hybrid_coverage_incomplete"));});}finally{await seed();}
  });
  await t.test("template เปลี่ยนหลัง query vector ไม่ใช้ผลเก่า",async()=>{
   const e=createProductHybridEngine(config(),provider()),r=request({query:"คำที่ไม่มีในข้อมูล"}),prepared=await reader(async()=>e.prepare(["TV0108"],r,await e.readiness(dbc,["TV0108"],r)));
   await db.exec("UPDATE product_search_documents SET template_version='changed' WHERE product_id=100002");try{const a=await reader(()=>e.search(dbc,["TV0108"],r,prepared));assert.equal(a.semantic_available,false);}finally{await seed();}
  });
  await t.test("model ปิดหรือ SHA ต่างไม่ใช้คะแนนเก่า",async()=>{
   await db.exec("UPDATE embedding_models SET is_active=false WHERE id='EM0001'");try{assert.equal((await reader(()=>hybrid())).semantic_available,false);}finally{await db.exec("UPDATE embedding_models SET is_active=true WHERE id='EM0001'");}
   assert.equal((await reader(()=>hybrid(request(),["TV0108"],"[1,0,0]","a".repeat(64)))).semantic_available,false);
  });
  await t.test("ผิดมิติ ศูนย์ และ norm ผิดไม่ผ่าน SQL",async()=>reader(async()=>{for(const v of ["[1,0]","[0,0,0]","[1,1,0]"])await assert.rejects(()=>hybrid(request(),["TV0108"],v));}));
  await t.test("anonymous และตารางเวกเตอร์ยังถูกจำกัดสิทธิ์",async()=>{
   await reader(async()=>{await assert.rejects(()=>db.exec("SELECT * FROM product_embeddings"),x=>typeof x==="object" && x!==null && "code" in x && x.code==="42501");});
   await db.exec("SET ROLE anon");try{await assert.rejects(()=>hybrid(),x=>typeof x==="object" && x!==null && "code" in x && x.code==="42501");}finally{await db.exec("RESET ROLE");}
  });
  await t.test("provider ไม่พร้อมใช้ keyword พร้อมเหตุผล แม้ไม่มี strict",async()=>reader(async()=>{
   const p=provider();p.embed=async()=>{throw new EmbeddingError("network_error");};const r=request({query:"คำที่ไม่มีในข้อมูล"}),s=await createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),p)}).search(r),out=mapCatalogSearchResponse(s,"fallback-fixture");
   assert.equal(out.status,"ok");if(out.status==="ok"){assert.equal(out.semantic_available,false);assert.ok(out.warnings.some(w=>w.code==="product_hybrid_network_error"));assert.ok(out.results.every(x=>x.match_type==="alternative"));}
  }));
  await t.test("HTTP คืน alternative-only แบบ v1 และตรวจ auth/ชนิดค่าก่อนโมเดล",async()=>reader(async()=>{
   const p=provider(),old=p.embed;p.embed=async input=>{assert.equal(connections,0);assert.equal(inTransaction,false);return old(input);};
   const catalog=createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),p)}),token="x".repeat(43),app=buildApp({database:null,api:{token},catalog,logger:false});const r=request({query:"คำที่ไม่มีในข้อมูล",limit:5});
   try{
    assert.equal((await app.inject({method:"POST",url:"/api/search",payload:r})).statusCode,401);assert.equal(p.calls,0);
    assert.equal((await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:{...r,filters:{category_ids:["TV0108"],price:{max:"1"}}}})).statusCode,400);assert.equal(p.calls,0);
    const result=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:r}),body=result.json();assert.equal(result.statusCode,200);assert.equal(body.version,"1");assert.equal(body.meta.total_matches,0);assert.equal(body.meta.returned_count,5);assert.equal(body.semantic_available,true);assert.equal(validateToolSearchResponse(body).valid,true);assert.ok(body.results.every((x:{match_type:string;unmet_filters:unknown[]})=>x.match_type==="alternative" && x.unmet_filters.length>0));assert.ok(!JSON.stringify(body).includes('"rrf_score"') && !JSON.stringify(body).includes('"embedding"'));
   }finally{await app.close();}
  }));
  await t.test("SQL ใช้ไม่ได้เป็น 503 ไม่ใช่ผลค้นว่าง",async()=>{
   await db.exec("ALTER FUNCTION catalog_search.search_products_hybrid_v2(text[],jsonb,extensions.vector,text,text,text) RENAME TO fixture_missing_hybrid");
   try{await reader(async()=>{const app=buildApp({database:null,api:{token:"x".repeat(43)},catalog:createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),provider())}),logger:false});try{const r=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+"x".repeat(43)},payload:request()});assert.equal(r.statusCode,503);assert.equal(r.json().status,"search_unavailable");assert.ok(!r.body.includes("does not exist"));}finally{await app.close();}});}finally{await db.exec("ALTER FUNCTION catalog_search.fixture_missing_hybrid(text[],jsonb,extensions.vector,text,text,text) RENAME TO search_products_hybrid_v2");}
  });
  await t.test("ย้อน0010ไม่ลบเวกเตอร์และยังเรียก SQL รุ่นก่อนหน้าได้",async()=>{
   const n=(await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings")).rows[0]!.n;await applyMigrations(client,dir,"down");assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings")).rows[0]!.n,n);
   await reader(async()=>{const old=await db.query("SELECT catalog_search.search_products_hybrid_v1($1::text[],$2::jsonb,$3::extensions.vector,$4,$5,$6)",[["TV0108"],JSON.stringify(request()),"[1,0,0]","EM0001",hash,sha]);assert.equal(old.rows.length,1);});assert.deepEqual(await applyMigrations(client,dir,"up"),["0010_hybrid_alternatives"]);
  });
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

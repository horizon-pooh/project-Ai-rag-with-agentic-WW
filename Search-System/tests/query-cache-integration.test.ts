import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve,join} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {vector} from "@electric-sql/pglite-pgvector";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import {pgcrypto} from "@electric-sql/pglite/contrib/pgcrypto";
import {Client,type ClientBase,type PoolClient,type Pool} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import type {DatabaseIdentity} from "../src/database/postgres.js";
import {registerEmbeddingModel} from "../src/repositories/embedding-backfill.repository.js";
import {createQueryEmbeddingCache,type QueryEmbeddingCache} from "../src/embeddings/query-embedding-cache.js";
import {DEFAULT_QUERY_CACHE_CONFIG,type QueryCacheConfig} from "../src/config/query-cache.config.js";
import {embeddingConfigurationHash,DEFAULT_EMBEDDING_BOUNDS} from "../src/config/embedding.config.js";
import type {ProductHybridConfig} from "../src/config/product-hybrid.config.js";
import {EmbeddingError,type EmbeddingProfile,type EmbeddingResult} from "../src/embeddings/embedding.types.js";
import {createProductHybridEngine} from "../src/services/product-hybrid.service.js";
import {createCategorySemanticEngine,type QueryEmbeddingProvider} from "../src/services/category-semantic.service.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import {mapCatalogSearchResponse} from "../src/validation/catalog-response.mapper.js";
import {parseToolSearchRequest} from "../src/validation/tool-search.validation.js";
import {resolveCategoryFilters} from "../src/services/category-selector.service.js";
import type {CatalogPool} from "../src/types/catalog-read.types.js";
import {buildApp} from "../src/app.js";
const profile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};
const hash=embeddingConfigurationHash(profile),sha="b".repeat(64);
const config=():ProductHybridConfig=>({profile:structuredClone(profile),bounds:{...DEFAULT_EMBEDDING_BOUNDS},baseUrl:"http://127.0.0.1:18081/v1",apiKey:undefined,modelId:"EM0001",artifactSha256:sha,configurationHash:hash});
const result=():EmbeddingResult=>({provider:"llama_cpp",model:profile.model,dimensions:3,configurationHash:hash,purpose:"query",vectors:[[1,0,0]],usage:{promptTokens:4,totalTokens:4},attempts:1,tokenCounts:[4]});
const request=(patch:Record<string,unknown>={})=>{const r=parseToolSearchRequest({version:"1",query:"ทีวี",semantic_query:"ดูหนังในบ้าน",filters:{category_ids:["TV0108"],brand_queries:["LG"],feature_ids_all:["FT0074"],price:{max:25000},specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}]},limit:100,...patch});if(!r.valid)throw new Error("Invalid test request");return r.value;};
function source(){const p:QueryEmbeddingProvider & {calls:number;fail:boolean}={calls:0,fail:false,async embed(){p.calls++;if(p.fail)throw new EmbeddingError("network_error");return result();}};return p;}
const native=process.env.QUERY_CACHE_TEST_MODE==="native";
async function fixture():Promise<PGlite>{
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.QUERY_CACHE_TEST_HOST;if(!host?.startsWith("/private/tmp/"))throw new Error("Require private test socket");
 const c=new Client({host,port:Number(process.env.QUERY_CACHE_TEST_PORT??55444),database:"catalog_cache_test_c24",user:"catalog_test_admin"});await c.connect();
 const i=(await c.query("SELECT current_database() db,current_user u,current_setting('server_version') v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) su")).rows[0];
 if(i.db!=="catalog_cache_test_c24" || i.u!=="catalog_test_admin" || !i.su || !/^18\.6(?:\D|$)/.test(i.v)){await c.end();throw new Error("Wrong test database");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}
test("cache ไม่ข้ามการตรวจฐานและไม่เก็บผลสินค้า",async t=>{
 const db=await fixture(),dir=await mkdtemp(join(tmpdir(),"query-cache-integration-"));let connections=0,inTransaction=false;
 const client={async query(q:string|{text:string;values?:unknown[]},values?:unknown[]){const sql=typeof q==="string"?q:q.text,args=values??(typeof q==="object"?q.values:undefined);const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);if(/^BEGIN/.test(sql))inTransaction=true;if(/^(COMMIT|ROLLBACK)/.test(sql))inTransaction=false;return {rows:r?.rows??[],rowCount:r?.affectedRows??0};}} as unknown as ClientBase;
 const dbc=client as unknown as PoolClient,pool={async connect(){connections++;let released=false;return {query:client.query.bind(client),release(){if(!released){released=true;connections--;}}};}} as unknown as CatalogPool;
 const reader=async<T>(f:()=>Promise<T>)=>{await db.exec("SET ROLE search_api");try{return await f();}finally{await db.exec("RESET ROLE");}};
 const scene=(patch:Partial<QueryCacheConfig>={},runtime:{now?:()=>number}={})=>{
  const cache=createQueryEmbeddingCache({...DEFAULT_QUERY_CACHE_CONFIG,...patch},runtime),p=source(),engine=createProductHybridEngine(config(),p,{queryCache:cache});
  return {cache,p,engine,catalog:createPostgresCatalogRepository(pool,{productHybrid:engine})};
 };
 const seed=async()=>{
  await db.query(`INSERT INTO product_embeddings(product_id,model_id,embedding,content_hash,template_version,status,embedded_at)
   SELECT product_id,'EM0001','[1,0,0]'::extensions.vector,content_hash,template_version,'ready',now() FROM product_search_documents
   ON CONFLICT(product_id,model_id) DO UPDATE SET embedding=excluded.embedding,content_hash=excluded.content_hash,template_version=excluded.template_version,status='ready',embedded_at=now(),error_message=null`);
 };
 try{
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const f of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])await db.exec(await readFile(resolve("tests/fixtures",f),"utf8"));
  for(const f of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql","0008_semantic_category_resolution.sql","0009_product_hybrid_search.sql","0010_hybrid_alternatives.sql"])await copyFile(resolve("migrations",f),join(dir,f));
  await applyMigrations(client,dir,"up");await db.exec("BEGIN");await registerEmbeddingModel(dbc,"EM0001",profile,sha);await db.exec("COMMIT");await seed();
  await db.query("INSERT INTO category_embeddings(category_id,model_id,embedding,document_hash,template_version,status,embedded_at) SELECT category_id,'EM0001','[1,0,0]'::extensions.vector,source_hash,template_version,'ready',now() FROM category_search_documents");
  await t.test("คำเดิม hit แต่ยัง query ฐานและคืนข้อมูลตามปัจจุบัน",async()=>{
   const s=scene();try{const a=await reader(()=>s.catalog.search(request()));assert.equal(a.search!.meta!.total_matches,3);
    await db.exec("UPDATE products SET price=99000 WHERE id=100005");
    try{const b=await reader(()=>s.catalog.search(request()));assert.equal(s.p.calls,1);assert.equal(b.search!.meta!.total_matches,2);assert.equal(b.products.find(p=>p.article_id===100005)!.price,99000);assert.equal(b.search!.results.find(p=>p.article_id===100005)!.match_type,"alternative");assert.ok(b.search!.warnings.some(w=>w.code==="product_query_embedding_cached"));}finally{await db.exec("UPDATE products SET price=24000 WHERE id=100005");}
   }finally{await s.cache.close();}
  });
  await t.test("เปลี่ยนตัวกรองราคาไม่ใช้ผลค้นเก่าจาก cache",async()=>{
   const s=scene();try{await reader(()=>s.catalog.search(request()));const r=await reader(()=>s.catalog.search(request({filters:{category_ids:["TV0108"],price:{max:1}}})));assert.equal(s.p.calls,1);assert.equal(r.search!.meta!.total_matches,1);assert.equal(r.search!.results[0]!.article_id,100007);assert.ok(r.search!.results.slice(1).every(x=>x.match_type==="alternative"));}finally{await s.cache.close();}
  });
  await t.test("สินค้าที่กลายเป็น draft ไม่หลุดผ่าน cache",async()=>{
   const s=scene();try{await reader(()=>s.catalog.search(request()));await db.exec("UPDATE products SET status='draft' WHERE id=100000");
    try{const r=await reader(()=>s.catalog.search(request()));assert.equal(s.p.calls,1);assert.ok(!r.products.some(p=>p.article_id===100000));assert.equal(await reader(()=>s.catalog.findDetails(100000)),null);}finally{await db.exec("UPDATE products SET status='published' WHERE id=100000");}
   }finally{await s.cache.close();}
  });
  await t.test("source stale บล็อก cache จนเวกเตอร์สินค้าอัปเดต",async()=>{
   const s=scene();try{await reader(()=>s.catalog.search(request()));await db.exec("UPDATE product_search_documents SET template_version='cache-source-v2' WHERE product_id=100000");
    const a=await reader(()=>s.catalog.search(request()));assert.equal(a.search!.semantic_available,false);assert.equal(s.cache.stats().hits,0);assert.equal(s.p.calls,1);assert.ok(a.search!.warnings.some(w=>w.code==="product_hybrid_coverage_incomplete"));
    await seed();const b=await reader(()=>s.catalog.search(request()));assert.equal(b.search!.semantic_available,true);assert.equal(s.p.calls,1);assert.equal(s.cache.stats().hits,1);
   }finally{await seed();await s.cache.close();}
  });
  await t.test("model inactive บล็อก cache แต่ profile เดิมใช้ต่อได้เมื่อเปิดใหม่",async()=>{
   const s=scene();try{await reader(()=>s.catalog.search(request()));await db.exec("UPDATE embedding_models SET is_active=false WHERE id='EM0001'");
    try{const r=await reader(()=>s.catalog.search(request()));assert.equal(r.search!.semantic_available,false);assert.ok(r.search!.warnings.some(w=>w.code==="product_hybrid_model_unavailable"));assert.equal(s.cache.stats().hits,0);}finally{await db.exec("UPDATE embedding_models SET is_active=true WHERE id='EM0001'");}
    assert.equal((await reader(()=>s.catalog.search(request()))).search!.semantic_available,true);assert.equal(s.p.calls,1);
   }finally{await s.cache.close();}
  });
  await t.test("profile version ไม่ตรงฐานไม่ใช้ cache ของรุ่นก่อน",async()=>{
   const s=scene();try{await reader(()=>s.catalog.search(request()));const c=config();c.profile.version="fixture-v2";c.configurationHash=embeddingConfigurationHash(c.profile);const p=source(),catalog=createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(c,p,{queryCache:s.cache})});const r=await reader(()=>catalog.search(request()));assert.equal(r.search!.semantic_available,false);assert.equal(p.calls,0);assert.equal(s.cache.stats().hits,0);}finally{await s.cache.close();}
  });
  await t.test("source เปลี่ยนหลังอ่าน cache ยังถูก SQL ตรวจใน snapshot สุดท้าย",async()=>{
   const s=scene();try{await reader(()=>s.catalog.search(request()));const r=request(),prepared=await reader(async()=>s.engine.prepare(["TV0108"],r,await s.engine.readiness(dbc,["TV0108"],r)));assert.ok(prepared.warning?.code==="product_query_embedding_cached");
    await db.exec("UPDATE product_search_documents SET template_version='cache-source-v3' WHERE product_id=100001");
    const out=await reader(()=>s.engine.search(dbc,["TV0108"],r,prepared));assert.equal(out.semantic_available,false);assert.ok(!out.warnings.some(w=>w.code==="product_query_embedding_cached"));
   }finally{await seed();await s.cache.close();}
  });
  await t.test("cache ที่ยังตรงใช้ได้แม้ provider หยุด แต่หมดอายุแล้วกลับ keyword",async()=>{
   let time=0;const s=scene({ttlMs:100},{now:()=>time});try{await reader(()=>s.catalog.search(request()));s.p.fail=true;const cached=await reader(()=>s.catalog.search(request()));assert.equal(cached.search!.semantic_available,true);assert.equal(s.p.calls,1);
    time=101;const failed=await reader(()=>s.catalog.search(request()));assert.equal(failed.search!.semantic_available,false);assert.ok(failed.search!.warnings.some(w=>w.code==="product_hybrid_network_error"));assert.equal(s.p.calls,2);
    s.p.fail=false;const recovered=await reader(()=>s.catalog.search(request()));assert.equal(recovered.search!.semantic_available,true);assert.equal(s.p.calls,3);
   }finally{await s.cache.close();}
  });
  await t.test("capacity เต็มกลับ keyword ไม่รอคิวและไม่ค้าง connection",async()=>{
   const s=scene({maxInFlight:1});let resolvePending!:(r:EmbeddingResult)=>void;const pending=new Promise<EmbeddingResult>(r=>{resolvePending=r;});s.p.embed=async()=>{assert.equal(connections,0);assert.equal(inTransaction,false);return pending;};
   try{const r=request(),state=await reader(()=>s.engine.readiness(dbc,["TV0108"],r));const first=s.engine.prepare(["TV0108"],r,state);await new Promise<void>(r=>setImmediate(r));
    const out=await reader(()=>s.catalog.search(request({semantic_query:"อีกคำที่ต่างกัน"})));assert.equal(out.search!.semantic_available,false);assert.ok(out.search!.warnings.some(w=>w.code==="product_hybrid_capacity_exceeded"));resolvePending(result());await first;
   }finally{resolvePending?.(result());await s.cache.close();}
  });
  await t.test("ปิด cache แล้วยังค้น hybrid ได้และ provider ถูกเรียกใหม่",async()=>{
   const s=scene({enabled:false});try{assert.equal((await reader(()=>s.catalog.search(request()))).search!.semantic_available,true);assert.equal((await reader(()=>s.catalog.search(request()))).search!.semantic_available,true);assert.equal(s.p.calls,2);assert.equal(s.cache.stats().entries,0);}finally{await s.cache.close();}
  });
  await t.test("หมวดและสินค้าร่วม cache เดียวกันได้เมื่อข้อความและ profile ตรง",async()=>{
   const cache=createQueryEmbeddingCache(),cp=source(),pp=source(),category=createCategorySemanticEngine({...config(),policy:{autoSelect:true,minScore:0.8,minGap:0.1,calibrationId:"fixture-only"}},cp,{queryCache:cache});
   const catalog=createPostgresCatalogRepository(pool,{categorySemantic:category,productHybrid:createProductHybridEngine(config(),pp,{queryCache:cache})}),phrase="ทีวี OLED สำหรับดูหนัง",r=request({semantic_query:phrase,filters:{category_query:phrase}});
   try{await reader(()=>catalog.search(r));const snap=await reader(()=>catalog.search(r)),response=mapCatalogSearchResponse(snap,"shared-cache-fixture");assert.equal(cp.calls,1);assert.equal(pp.calls,0);assert.equal(response.status,"ok");if(response.status==="ok"){assert.ok(response.warnings.some(w=>w.code==="category_query_embedding_cached"));assert.ok(response.warnings.some(w=>w.code==="product_query_embedding_cached"));assert.equal(response.semantic_available,true);}}finally{await cache.close();}
  });
  await t.test("หมวด exact ใหม่ชนะ route ที่เคยเลือกด้วยเวกเตอร์",async()=>{
   const s=scene(),phrase="ทีวี OLED สำหรับดูหนัง",cat=createCategorySemanticEngine({...config(),policy:{autoSelect:true,minScore:0.8,minGap:0.1,calibrationId:"fixture-only"}},s.p,{queryCache:s.cache}),catalog=createPostgresCatalogRepository(pool,{categorySemantic:cat,productHybrid:s.engine}),r=request({semantic_query:phrase,filters:{category_query:phrase}});
   try{const before=await reader(()=>catalog.search(r));assert.deepEqual(before.category_resolution.category_ids,["TV0108"]);await db.query("UPDATE categories SET aliases=aliases||ARRAY[$1] WHERE id='TV0109'",[phrase]);
    try{const after=await reader(()=>catalog.search(r));assert.deepEqual(after.category_resolution.category_ids,["TV0109"]);assert.deepEqual(after.products.map(p=>p.article_id),[100011]);assert.equal(s.p.calls,1);}finally{await db.query("UPDATE categories SET aliases=array_remove(aliases,$1) WHERE id='TV0109'",[phrase]);}
   }finally{await s.cache.close();}
  });
  await t.test("HTTP ตรวจ token ก่อน cache และไม่เปิด stats ผ่าน health",async()=>{
   const s=scene(),token="x".repeat(43),app=buildApp({database:null,api:{token},catalog:s.catalog,queryCache:s.cache,logger:false});
   try{await reader(async()=>{const first=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:request()});assert.equal(first.statusCode,200);
    const denied=await app.inject({method:"POST",url:"/api/search",payload:request()});assert.equal(denied.statusCode,401);assert.equal(s.cache.stats().hits,0);
    const second=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:request()});assert.equal(second.statusCode,200);assert.equal(second.json().semantic_available,true);assert.ok(second.json().warnings.some((w:{code:string})=>w.code==="product_query_embedding_cached"));assert.equal(s.p.calls,1);
    const health=await app.inject({url:"/health"});assert.deepEqual(health.json(),{status:"ok",service:"search-system"});});
   }finally{await app.close();}assert.equal(s.cache.stats().entries,0);
  });
  await t.test("default app factories ใช้ cache ตัวเดียวและปิดตาม app",async()=>{
   const cache=createQueryEmbeddingCache(),p=source();let wraps=0,closed=0;
   const observed:QueryEmbeddingCache={wrap(_raw,i){wraps++;return cache.wrap(p,i);},stats:cache.stats,async close(){closed++;await cache.close();}};
   const values={SEARCH_CATEGORY_SEMANTIC_ENABLED:"true",SEARCH_CATEGORY_SEMANTIC_MODEL_ID:"EM0001",SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT:"false",SEARCH_PRODUCT_HYBRID_ENABLED:"true",SEARCH_PRODUCT_HYBRID_MODEL_ID:"EM0001",EMBEDDING_ARTIFACT_SHA256:sha,
    EMBEDDING_PROVIDER:"llama_cpp",EMBEDDING_MODEL:profile.model,EMBEDDING_DIMENSIONS:"3",EMBEDDING_CONTEXT_TOKENS:"2048",EMBEDDING_CONFIG_VERSION:profile.version,EMBEDDING_RUNTIME_VERSION:profile.runtimeVersion,EMBEDDING_ARTIFACT:profile.artifact,EMBEDDING_DISTANCE_METRIC:"cosine",EMBEDDING_POOLING:"last",EMBEDDING_NORMALIZATION:"server_l2",EMBEDDING_BASE_URL:"http://127.0.0.1:18081/v1",EMBEDDING_QUERY_PREFIX_JSON:'""',EMBEDDING_DOCUMENT_PREFIX_JSON:'""'};
   const previous=new Map(Object.keys(values).map(k=>[k,process.env[k]]));Object.assign(process.env,values);
   let app:ReturnType<typeof buildApp>|undefined;
   try{app=buildApp({database:{pool:pool as unknown as Pool,async check(){return {} as DatabaseIdentity;},async close(){}},api:{token:"x".repeat(43)},queryCache:observed,logger:false});assert.equal(wraps,2);
    await reader(async()=>{for(let i=0;i<2;i++){const r=await app!.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+"x".repeat(43)},payload:request()});assert.equal(r.statusCode,200);}});assert.equal(p.calls,1);
   }finally{await app?.close();for(const [k,v] of previous){if(v===undefined)delete process.env[k];else process.env[k]=v;}}assert.equal(closed,1);
  });
  await t.test("มี cache ก็ไม่ปิดบัง SQL error เป็นผลสำเร็จเก่า",async()=>{
   const s=scene(),token="x".repeat(43),app=buildApp({database:null,api:{token},catalog:s.catalog,queryCache:s.cache,logger:false});
   try{await reader(()=>s.catalog.search(request()));await db.exec("ALTER FUNCTION catalog_search.search_products_hybrid_v2(text[],jsonb,extensions.vector,text,text,text) RENAME TO cache_fixture_missing");
    try{await reader(async()=>{const r=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:request()});assert.equal(r.statusCode,503);assert.equal(r.json().status,"search_unavailable");assert.equal(s.p.calls,1);});}
    finally{await db.exec("ALTER FUNCTION catalog_search.cache_fixture_missing(text[],jsonb,extensions.vector,text,text,text) RENAME TO search_products_hybrid_v2");}
   }finally{await app.close();}
  });
  await t.test("cache ไม่เพิ่มสิทธิ์อ่านเวกเตอร์หรือต้นทาง",async()=>reader(async()=>{
   for(const table of ["embedding_models","product_embeddings","category_embeddings"])await assert.rejects(()=>db.exec("SELECT * FROM "+table),e=>typeof e==="object" && e!==null && "code" in e && e.code==="42501");
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM products WHERE status='draft'")).rows[0]!.n,0);
  }));
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

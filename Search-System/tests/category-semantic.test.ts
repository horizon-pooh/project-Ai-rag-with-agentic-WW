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
import {readCategorySemanticConfig,type CategorySemanticConfig} from "../src/config/category-semantic.config.js";
import {EmbeddingError,type EmbeddingProfile} from "../src/embeddings/embedding.types.js";
import {createCategorySemanticEngine,type QueryEmbeddingProvider} from "../src/services/category-semantic.service.js";
import {resolveCategoryFilters} from "../src/services/category-selector.service.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import {mapCatalogSearchResponse} from "../src/validation/catalog-response.mapper.js";
import {buildApp} from "../src/app.js";
import {createToolApiService} from "../src/services/tool-api.service.js";
import type {CatalogPool} from "../src/types/catalog-read.types.js";
import type {SemanticCategoryEvidence} from "../src/types/category-semantic.types.js";
const profile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};
const hash=embeddingConfigurationHash(profile),sha="b".repeat(64);
const config=(auto=false):CategorySemanticConfig=>({profile:structuredClone(profile),bounds:{...DEFAULT_EMBEDDING_BOUNDS},baseUrl:"http://127.0.0.1:18081/v1",apiKey:undefined,
 modelId:"EM0001",artifactSha256:sha,configurationHash:hash,policy:{autoSelect:auto,minScore:auto?0.8:null,minGap:auto?0.1:null,calibrationId:auto?"fixture-only-not-production":null}});
function provider() {const p:QueryEmbeddingProvider & {calls:number}={calls:0,async embed(){p.calls++;return {provider:"llama_cpp",model:profile.model,dimensions:3,configurationHash:hash,purpose:"query",vectors:[[1,0,0]],usage:{promptTokens:5,totalTokens:5},attempts:1,tokenCounts:[5]};}};return p;}
const native=process.env.CATEGORY_SEMANTIC_TEST_MODE==="native";
async function fixture():Promise<PGlite>{
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.CATEGORY_SEMANTIC_TEST_HOST;if(!host?.startsWith("/private/tmp/"))throw new Error("Require private disposable Unix socket");
 const c=new Client({host,port:Number(process.env.CATEGORY_SEMANTIC_TEST_PORT??55441),database:"catalog_category_test_c21",user:"catalog_test_admin"});await c.connect();
 const r=(await c.query("SELECT current_database() db,current_user u,current_setting('server_version') v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) su")).rows[0];
 if(r.db!=="catalog_category_test_c21" || r.u!=="catalog_test_admin" || !r.su || !/^18\.6(?:\D|$)/.test(r.v)){await c.end();throw new Error("Wrong native test target");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}
test("category semantic SQL and service preserve lexical specificity with private exact vectors",async t=>{
 const db=await fixture(),dir=await mkdtemp(join(tmpdir(),"category-semantic-"));
 let inTransaction=false,connections=0;
 const client={async query(q:string|{text:string;values?:unknown[]},values?:unknown[]){const sql=typeof q==="string"?q:q.text,args=values??(typeof q==="object"?q.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);if(/^BEGIN/.test(sql))inTransaction=true;if(/^(COMMIT|ROLLBACK)/.test(sql))inTransaction=false;return {rows:r?.rows??[],rowCount:r?.affectedRows??0};}} as unknown as ClientBase;
 const dbc=client as unknown as PoolClient;
 const reader=async<T>(f:()=>Promise<T>)=>{await db.exec("SET ROLE search_api");try{return await f();}finally{await db.exec("RESET ROLE");}};
 const raw=async(query:string,vec="[1,0,0]",limit=8)=>((await db.query<{e:SemanticCategoryEvidence}>("SELECT catalog_search.category_semantic_candidates_v1($1,$2::extensions.vector,$3,$4,$5,$6) e",[query,vec,"EM0001",hash,sha,limit])).rows[0]!.e);
 const lexical=(q:string)=>resolveCategoryFilters(dbc,{category_query:q});
 const resolveSemantic=async(q:string,auto=false,p=provider())=>reader(async()=>{const e=createCategorySemanticEngine(config(auto),p),l=await lexical(q),s=await e.readiness(dbc);return e.resolve(dbc,l,await e.prepare(l,s));});
 const seed=async()=>{
  await db.query(`INSERT INTO category_embeddings(category_id,model_id,embedding,document_hash,template_version,status,embedded_at)
   SELECT d.category_id,'EM0001',CASE WHEN d.category_id='TV0108' THEN '[1,0,0]' WHEN d.category_id='TV0109' THEN '[0.6,0.8,0]'
    WHEN d.category_id='TV0201' THEN '[0.99,0.14106736,0]' ELSE '[0,1,0]' END::extensions.vector,d.source_hash,d.template_version,'ready',now() FROM category_search_documents d
   ON CONFLICT(category_id,model_id) DO UPDATE SET embedding=excluded.embedding,document_hash=excluded.document_hash,template_version=excluded.template_version,status='ready',embedded_at=now(),error_message=null`);
 };
 try {
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const f of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])await db.exec(await readFile(resolve("tests/fixtures",f),"utf8"));
  for(const f of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql","0008_semantic_category_resolution.sql"])await copyFile(resolve("migrations",f),join(dir,f));
  await t.test("migration applies8 and replays without recreating storage",async()=>{assert.equal((await applyMigrations(client,dir,"up")).length,8);assert.deepEqual(await applyMigrations(client,dir,"up"),[]);});
  await t.test("missing model returns unavailable rather than fabricated evidence",async()=>reader(async()=>{assert.equal((await createCategorySemanticEngine(config(),provider()).readiness(dbc)).status,"model_unavailable");}));
  await db.exec("BEGIN");await registerEmbeddingModel(dbc,"EM0001",profile,sha);await db.exec("COMMIT");
  await t.test("incomplete coverage gates vectors before any embedding call",async()=>{
   const p=provider(),e=createCategorySemanticEngine(config(),p);
   await reader(async()=>{const l=await lexical("ภาพเคลื่อนไหวสำหรับห้องนั่งเล่น"),s=await e.readiness(dbc);assert.equal(s.status,"coverage_incomplete");const r=await e.resolve(dbc,l,await e.prepare(l,s));assert.equal(r.status,"needs_clarification");assert.equal(p.calls,0);assert.ok(r.selector_issues?.some(i=>i.reason==="category_semantic_coverage_incomplete"));});
  });
  await seed();
  await t.test("full fresh coverage makes read-only capability ready",async()=>reader(async()=>{const r=await createCategorySemanticEngine(config(),provider()).readiness(dbc);assert.equal(r.status,"ready");assert.equal(r.total,r.fresh);}));
  await t.test("search reader can execute bounded evidence but cannot SELECT private vectors/models",async()=>reader(async()=>{
   const e=await raw("ภาพเคลื่อนไหวสำหรับห้องนั่งเล่น");assert.equal(e.status,"ready");assert.ok(e.candidates.length<=8);assert.ok(!JSON.stringify(e).includes('"embedding"'));
   for(const table of ["category_embeddings","embedding_models","category_search_documents"])await assert.rejects(()=>db.exec("SELECT * FROM "+table),x=>typeof x==="object" && x!==null && "code" in x && x.code==="42501");
  }));
  await t.test("anonymous cannot invoke either capability or read vectors",async()=>{await db.exec("SET ROLE anon");try{await assert.rejects(()=>raw("ทีวีเพื่อดูภาพยนตร์"),x=>typeof x==="object" && x!==null && "code" in x && x.code==="42501");await assert.rejects(()=>db.query("SELECT catalog_search.category_semantic_state_v1($1,$2,$3)",["EM0001",hash,sha]));}finally{await db.exec("RESET ROLE");}});
  await t.test("explicit OLED anchor excludes QLED and TV accessories regardless of higher vectors",async()=>reader(async()=>{const e=await raw("ต้องการทีวี OLED สำหรับดูหนัง");assert.deepEqual(e.anchor_roots,["TV0108"]);assert.deepEqual(e.candidates.map(c=>c.category_id),["TV0108"]);}));
  await t.test("QLED boundary never mistakes NEO QLED for an unqualified scope",async()=>reader(async()=>{const e=await raw("ต้องการทีวี NEO QLED สำหรับดูหนัง");assert.deepEqual(e.anchor_roots,["TV0107"]);assert.deepEqual(e.candidates.map(c=>c.category_id),["TV0107"]);}));
  await t.test("broad TV anchor keeps every television leaf and excludes mounts",async()=>reader(async()=>{const e=await raw("ทีวีสำหรับดูหนังในบ้าน");assert.deepEqual(new Set(e.candidates.map(c=>c.category_id)),new Set(["TV0107","TV0108","TV0109"]));}));
  await t.test("accessory anchor does not search televisions",async()=>reader(async()=>{const e=await raw("ขาแขวนทีวีสำหรับติดผนัง");assert.deepEqual(e.candidates.map(c=>c.category_id),["TV0201"]);}));
  await t.test("unknown category code is an identity lock",async()=>reader(async()=>{const e=await raw("ZZ9999");assert.equal(e.status,"identity_lock");assert.deepEqual(e.candidates,[]);const p=provider(),r=await resolveSemantic("ZZ9999",true,p);assert.equal(r.status,"needs_clarification");assert.equal(p.calls,0);}));
  await t.test("invalid dimension/zero vector/limit rejected in SQL",async()=>reader(async()=>{for(const v of ["[1,0]","[0,0,0]","[1,1,0]"])await assert.rejects(()=>raw("ภาพเคลื่อนไหว",v));await assert.rejects(()=>raw("ภาพเคลื่อนไหว","[1,0,0]",100));}));
  await t.test("default suggestions remain clarification and expose candidate IDs without false probability",async()=>{const r=await resolveSemantic("ทีวีสำหรับดูหนังในบ้าน");assert.equal(r.status,"needs_clarification");assert.equal(r.groups[0]!.reason,"semantic_candidates_require_confirmation");assert.deepEqual(r.category_ids,[]);assert.equal(r.groups[0]!.candidates[0]!.match_kind,"semantic");});
  await t.test("explicit fixture calibration selects a leaf and preserves OLED constraint",async()=>{const r=await resolveSemantic("ทีวี OLED สำหรับดูหนัง",true);assert.equal(r.status,"resolved");assert.deepEqual(r.category_ids,["TV0108"]);assert.equal(r.groups[0]!.reason,"semantic_calibrated");});
  await t.test("score gap ties require clarification even in calibrated mode",async()=>{await db.exec("UPDATE category_embeddings SET embedding='[1,0,0]' WHERE category_id='TV0109'");const r=await resolveSemantic("ทีวีสำหรับดูหนัง",true);assert.equal(r.status,"needs_clarification");await seed();});
  await t.test("multi-phrase OR resolves distinct scopes rather than broadening each subtype",async()=>{const r=await resolveSemantic("ทีวี OLED สำหรับดูหนัง | ขาแขวนทีวีสำหรับติดผนัง",true);assert.equal(r.status,"resolved");assert.deepEqual(r.category_ids,["TV0108","TV0201"]);});
  await t.test("two unrelated anchors inside one phrase require clarification",async()=>{const r=await resolveSemantic("ทีวี OLED และ ขาแขวนทีวีสำหรับบ้าน",true);assert.equal(r.status,"needs_clarification");assert.equal(r.groups[0]!.reason,"semantic_anchor_ambiguity");});
  await t.test("exact ambiguous aliases cannot be overridden with a vector",async()=>{const p=provider(),e=createCategorySemanticEngine(config(true),p),l=await lexical("จอพิเศษ");assert.equal(e.hasWork(l),false);assert.equal(p.calls,0);});
  await t.test("large fuzzy candidate set may use semantics but large exact ambiguity may not",async()=>{
   const l=await lexical("ทีวีสำหรับดูหนัง"),sample=(await lexical("OLED")).groups[0]!.candidates[0]!;
   l.status="needs_clarification";l.groups[0]={...l.groups[0]!,status:"needs_clarification",reason:"candidate_limit_exceeded",candidates:[{...sample,match_kind:"similar",total_candidates:20}]};
   const engine=createCategorySemanticEngine(config(),provider());assert.equal(engine.hasWork(l),true);
   l.groups[0]!.candidates[0]!.match_kind="exact";assert.equal(engine.hasWork(l),false);
  });
  await t.test("wrong provider configuration hash falls back without querying a fake vector",async()=>{
   const p=provider(),old=p.embed;p.embed=async input=>({...await old(input),configurationHash:"a".repeat(64)});
   const r=await resolveSemantic("ทีวีสำหรับดูหนัง",true,p);assert.equal(r.status,"needs_clarification");assert.ok(r.selector_issues?.some(i=>i.reason==="category_semantic_invalid_response"));
  });
  await t.test("provider failure retains lexical evidence with truthful diagnostic",async()=>{const p=provider();p.embed=async()=>{throw new EmbeddingError("network_error");};const r=await resolveSemantic("ทีวีสำหรับดูหนัง",false,p);assert.equal(r.resolver_version,"category-lexical-v1");assert.ok(r.selector_issues?.some(i=>i.reason==="category_semantic_network_error"));});
  await t.test("partial multi-phrase provider success is discarded after later failure",async()=>{const p=provider(),old=p.embed;p.embed=async input=>{if(p.calls)throw new EmbeddingError("timeout");return old(input);};const r=await resolveSemantic("ทีวีสำหรับดูหนัง | ขาแขวนทีวีสำหรับติดผนัง",true,p);assert.equal(r.status,"needs_clarification");assert.equal(r.resolver_version,"category-lexical-v1");assert.deepEqual(r.category_ids,[]);});
  await t.test("source/template staleness after HTTP prevents semantic scope selection",async()=>{
   const e=createCategorySemanticEngine(config(true),provider()),l=await lexical("ทีวีสำหรับดูหนัง"),prepared=await reader(async()=>e.prepare(l,await e.readiness(dbc)));
   await db.exec("UPDATE category_search_documents SET template_version='changed-after-query' WHERE category_id='TV0108'");
   const r=await reader(()=>e.resolve(dbc,l,prepared));assert.equal(r.status,"needs_clarification");assert.ok(r.semantic_warnings?.some(w=>w.code==="category_semantic_coverage_incomplete"));await seed();
  });
  await t.test("missing source document is incomplete coverage, not silently excluded",async()=>{
   await db.exec("INSERT INTO categories VALUES('TV0199','TV0100',3,'หมวดไม่มีเอกสาร','Missing doc','{}',NULL)");
   assert.equal((await reader(()=>createCategorySemanticEngine(config(),provider()).readiness(dbc))).status,"coverage_incomplete");await db.exec("DELETE FROM categories WHERE id='TV0199'");
  });
  await t.test("model deactivation or SHA mismatch disables evidence",async()=>{
   await db.exec("UPDATE embedding_models SET is_active=false WHERE id='EM0001'");assert.equal((await raw("ทีวีสำหรับดูหนัง")).status,"model_unavailable");await db.exec("UPDATE embedding_models SET is_active=true WHERE id='EM0001'");
   const wrong=config();wrong.artifactSha256="a".repeat(64);assert.equal((await createCategorySemanticEngine(wrong,provider()).readiness(dbc)).status,"model_unavailable");
  });
  const pool={async connect(){connections++;let released=false;return {query:client.query.bind(client),release(){if(!released){released=true;connections--;}}};}} as unknown as CatalogPool;
  await t.test("repository releases SQL connection/snapshot before query embedding",async()=>reader(async()=>{
   const p=provider(),old=p.embed;p.embed=async input=>{assert.equal(inTransaction,false);assert.equal(connections,0);return old(input);};
   const r=await createPostgresCatalogRepository(pool,{categorySemantic:createCategorySemanticEngine(config(true),p)}).search({query:"",filters:{category_query:"ทีวี OLED สำหรับดูหนัง"},limit:5});
   assert.equal(r.category_resolution.status,"resolved");assert.ok(r.search);const mapped=mapCatalogSearchResponse(r,"fixture-request");assert.ok(mapped.status==="ok" || mapped.status==="no_matches");
   if(mapped.status==="ok" || mapped.status==="no_matches"){assert.equal(mapped.semantic_available,false);assert.ok(mapped.warnings.some(w=>w.code==="category_semantic_scope_selected"));assert.ok(mapped.warnings.some(w=>w.code==="semantic_unavailable"));}
  }));
  await t.test("exact labels and ID selectors never call embedding provider",async()=>reader(async()=>{
   const p=provider(),repo=createPostgresCatalogRepository(pool,{categorySemantic:createCategorySemanticEngine(config(true),p)});
   await repo.search({query:"",filters:{category_query:"OLED"}});await repo.search({query:"",filters:{category_ids:["TV0108"]}});
   const r=await repo.search({query:"",filters:{category_ids:["TV0108"],category_query:"QLED"}});assert.equal(r.category_resolution.status,"constraint_conflict");assert.equal(p.calls,0);
  }));
  await t.test("inferred query provenance does not claim literal-only after semantic selection",async()=>reader(async()=>{
   const repo=createPostgresCatalogRepository(pool,{categorySemantic:createCategorySemanticEngine(config(true),provider())});const result=await createToolApiService(repo).search({query:"ทีวี OLED สำหรับดูหนัง"},"fixture-request");
   assert.ok(result.status==="ok" || result.status==="no_matches");if(result.status==="ok" || result.status==="no_matches")assert.ok(result.warnings.some(w=>w.code==="category_inferred_from_query" && w.message.includes("เวกเตอร์")));
  }));
  await t.test("HTTP auth and strict JSON precede semantic lookup; suggestions keep v1 contract",async()=>reader(async()=>{
   const p=provider(),catalog=createPostgresCatalogRepository(pool,{categorySemantic:createCategorySemanticEngine(config(),p)});
   const token="x".repeat(43),app=buildApp({database:null,api:{token},catalog,logger:false});
   const payload={version:"1",query:"ทีวี",filters:{category_query:"ทีวีสำหรับดูหนัง"},limit:5};
   try{
    const denied=await app.inject({method:"POST",url:"/api/search",payload});assert.equal(denied.statusCode,401);assert.equal(p.calls,0);
    const bad=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload:{...payload,vector:[1,0,0]}});assert.equal(bad.statusCode,400);assert.equal(p.calls,0);
    const ok=await app.inject({method:"POST",url:"/api/search",headers:{authorization:"Bearer "+token},payload});assert.equal(ok.statusCode,200);
    const body=ok.json();assert.equal(body.status,"needs_clarification");assert.ok(body.clarifications.some((x:{reason:string})=>x.reason==="semantic_candidates_require_confirmation"));assert.equal(p.calls,1);
   }finally{await app.close();}
  }));
  await t.test("rollback removes only functions, preserves vectors and existing lexical resolver",async()=>{
   const n=(await db.query<{n:number}>("SELECT count(*)::integer n FROM category_embeddings")).rows[0]!.n;await applyMigrations(client,dir,"down");assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM category_embeddings")).rows[0]!.n,n);
   assert.equal((await lexical("OLED")).status,"resolved");assert.deepEqual(await applyMigrations(client,dir,"up"),["0008_semantic_category_resolution"]);
  });
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});
const env=()=>({SEARCH_CATEGORY_SEMANTIC_ENABLED:"true",SEARCH_CATEGORY_SEMANTIC_MODEL_ID:"EM0001",EMBEDDING_ARTIFACT_SHA256:sha,
 EMBEDDING_PROVIDER:"llama_cpp",EMBEDDING_MODEL:profile.model,EMBEDDING_DIMENSIONS:"3",EMBEDDING_CONTEXT_TOKENS:"2048",EMBEDDING_CONFIG_VERSION:"fixture-v1",EMBEDDING_RUNTIME_VERSION:profile.runtimeVersion,
 EMBEDDING_ARTIFACT:profile.artifact,EMBEDDING_DISTANCE_METRIC:"cosine",EMBEDDING_POOLING:"last",EMBEDDING_NORMALIZATION:"server_l2",EMBEDDING_BASE_URL:"http://127.0.0.1:18081/v1"});
test("semantic is disabled by default; enabled defaults to suggestions with no guessed thresholds",()=>{assert.equal(readCategorySemanticConfig({}),null);assert.equal(readCategorySemanticConfig({...env(),SEARCH_CATEGORY_SEMANTIC_ENABLED:"false"}),null);assert.equal(readCategorySemanticConfig(env())!.policy.autoSelect,false);});
for(const patch of [{SEARCH_CATEGORY_SEMANTIC_ENABLED:"yes"},{SEARCH_CATEGORY_SEMANTIC_MODEL_ID:"EM1"},{EMBEDDING_ARTIFACT_SHA256:"bad"},{SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT:"true"},{SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT:"true",SEARCH_CATEGORY_SEMANTIC_MIN_SCORE:"0.8",SEARCH_CATEGORY_SEMANTIC_MIN_GAP:"0",SEARCH_CATEGORY_SEMANTIC_CALIBRATION_ID:"test"}])test("reject invalid semantic configuration: "+Object.keys(patch).join(","),()=>assert.throws(()=>readCategorySemanticConfig({...env(),...patch})));
test("calibrated mode requires explicit finite score gap and record",()=>{const c=readCategorySemanticConfig({...env(),SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT:"true",SEARCH_CATEGORY_SEMANTIC_MIN_SCORE:"0.8",SEARCH_CATEGORY_SEMANTIC_MIN_GAP:"0.1",SEARCH_CATEGORY_SEMANTIC_CALIBRATION_ID:"fixture-only"})!;assert.equal(c.policy.minGap,0.1);});

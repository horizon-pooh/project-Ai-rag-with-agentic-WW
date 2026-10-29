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
import {EmbeddingError,type EmbeddingProfile,type EmbeddingResult} from "../src/embeddings/embedding.types.js";
import {createProductHybridEngine} from "../src/services/product-hybrid.service.js";
import {type QueryEmbeddingProvider} from "../src/services/category-semantic.service.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import type {CatalogPool} from "../src/types/catalog-read.types.js";
const profile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};
const hash=embeddingConfigurationHash(profile),sha="b".repeat(64);
const config=():ProductHybridConfig=>({profile:structuredClone(profile),bounds:{...DEFAULT_EMBEDDING_BOUNDS},baseUrl:"http://127.0.0.1:18081/v1",apiKey:undefined,modelId:"EM0001",artifactSha256:sha,configurationHash:hash});
const result=():EmbeddingResult=>({provider:"llama_cpp",model:profile.model,dimensions:3,configurationHash:hash,purpose:"query",vectors:[[1,0,0]],usage:{promptTokens:4,totalTokens:4},attempts:1,tokenCounts:[4]});
function source(){const p:QueryEmbeddingProvider & {calls:number;fail:boolean}={calls:0,fail:false,async embed(){p.calls++;if(p.fail)throw new EmbeddingError("network_error");return result();}};return p;}
const native=process.env.SEARCH_EVALUATION_TEST_MODE==="native";
async function fixture():Promise<PGlite>{
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.SEARCH_EVALUATION_TEST_HOST;if(!host?.startsWith("/private/tmp/"))throw new Error("Require private test socket");
 const c=new Client({host,port:Number(process.env.SEARCH_EVALUATION_TEST_PORT??55445),database:"catalog_evaluation_test_c25",user:"catalog_test_admin"});await c.connect();
 const i=(await c.query("SELECT current_database() db,current_user u,current_setting('server_version') v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) su")).rows[0];
 if(i.db!=="catalog_evaluation_test_c25" || i.u!=="catalog_test_admin" || !i.su || !/^18\.6(?:\D|$)/.test(i.v)){await c.end();throw new Error("Wrong test database");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}

import {writeFile} from "node:fs/promises";
import {createToolApiService} from "../src/services/tool-api.service.js";
import {parseEvaluationSuite,evaluateSuite} from "../src/evaluation/search-evaluation.js";
test("ประเมินชุดคำค้น fixture แยกจากคะแนนโมเดลจริง",async t=>{
 const db=await fixture(),dir=await mkdtemp(join(tmpdir(),"search-evaluation-"));let connections=0;
 const client={async query(q:string|{text:string;values?:unknown[]},values?:unknown[]){const sql=typeof q==="string"?q:q.text,args=values??(typeof q==="object"?q.values:undefined);const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows??[],rowCount:r?.affectedRows??0};}} as unknown as ClientBase;
 const dbc=client as unknown as PoolClient,pool={async connect(){connections++;let released=false;return {query:client.query.bind(client),release(){if(!released){released=true;connections--;}}};}} as unknown as CatalogPool;
 const reader=async<T>(f:()=>Promise<T>)=>{await db.exec("SET ROLE search_api");try{return await f();}finally{await db.exec("RESET ROLE");}};
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

 await db.exec("UPDATE product_embeddings SET embedding=CASE WHEN product_id=100006 THEN '[1,0,0]' WHEN product_id=100005 THEN '[0.6,0.8,0]' ELSE '[0,1,0]' END::extensions.vector");
 const suite=parseEvaluationSuite(JSON.parse(await readFile(resolve("tests/fixtures/search-evaluation-v1.json"),"utf8")));
 assert.ok(suite.cases.length>=100);
 const keyword=createToolApiService(createPostgresCatalogRepository(pool));
 const p=source(),hybrid=createToolApiService(createPostgresCatalogRepository(pool,{productHybrid:createProductHybridEngine(config(),p)}));
 const report=await evaluateSuite(suite,{keyword:r=>reader(()=>keyword.search(r,"evaluation-keyword")),hybrid:r=>reader(()=>hybrid.search(r,"evaluation-hybrid"))});
 for(const [engine,run] of Object.entries(report.runs))for(const score of run.cases)
  await t.test(engine+" "+score.id+" "+score.tags.join("/"),()=>assert.deepEqual(score.failures,[],score.status));
 assert.equal(report.complete,true);assert.equal(report.comparison.paired_cases,suite.cases.length);
 assert.ok(report.comparison.hybrid_ranked_pairs>0);assert.equal(connections,0);
 if(process.env.SEARCH_EVALUATION_REPORT_PATH)await writeFile(process.env.SEARCH_EVALUATION_REPORT_PATH,JSON.stringify(report,null,2)+"\n");
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

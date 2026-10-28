import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {vector} from "@electric-sql/pglite-pgvector";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import {pgcrypto} from "@electric-sql/pglite/contrib/pgcrypto";
import {Client,type ClientBase,type PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import {EmbeddingBackfillRepository,registerEmbeddingModel} from "../src/repositories/embedding-backfill.repository.js";
import {embeddingConfigurationHash} from "../src/config/embedding.config.js";
import {runEmbeddingBackfill,retryDelay,type BackfillProvider} from "../src/embeddings/backfill.runner.js";
import {EmbeddingError,type EmbeddingProfile} from "../src/embeddings/embedding.types.js";
import {backfillArguments} from "../src/embeddings/backfill.cli.js";
import {validateBackfillOptions,type BackfillOptions} from "../src/embeddings/backfill.types.js";
import {createLlamaEmbeddingProvider} from "../src/embeddings/llama-embedding.provider.js";
const profile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};
const sha="b".repeat(64),hash=embeddingConfigurationHash(profile);
const options=(patch:Partial<BackfillOptions>={}):BackfillOptions=>({kind:"category",modelId:"EM0001",limit:3,maxTokens:10000,maxSeconds:30,maxAttempts:3,leaseSeconds:120,...patch});
const native=process.env.EMBEDDING_BACKFILL_TEST_MODE==="native";
const nativeOptions=()=>({host:process.env.EMBEDDING_STORAGE_TEST_HOST,port:Number(process.env.EMBEDDING_STORAGE_TEST_PORT??55439),user:"catalog_test_admin",database:"catalog_embedding_test_c20"});
async function database():Promise<PGlite> {
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const o=nativeOptions();if(!o.host || !o.host.startsWith("/private/tmp/"))throw new Error("Require private disposable Unix test socket");
 const c=new Client(o);await c.connect();const i=(await c.query("SELECT current_database() AS db,current_user AS u,current_setting('server_version') AS v,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS su")).rows[0];
 if(i.db!=="catalog_embedding_test_c20" || i.u!=="catalog_test_admin" || !i.su || !/^18\.6(?:\D|$)/.test(i.v)){await c.end();throw new Error("Wrong native test target");}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}
function fakeProvider(tokens=6):BackfillProvider & {calls:number} {
 return {calls:0,async inspect(){return {tokenCounts:[tokens],configurationHash:hash,embeddingCalls:0};},async embed(){this.calls++;return {provider:"llama_cpp",model:profile.model,dimensions:3,configurationHash:hash,purpose:"document",vectors:[[1,0,0]],usage:{promptTokens:tokens,totalTokens:tokens},attempts:1,tokenCounts:[tokens]};}};
}
test("backfill uses existing storage, exact registry and fenced resumable published jobs",async t=>{
 const db=await database(),dir=await mkdtemp(join(tmpdir(),"backfill-tests-"));
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){const sql=typeof query==="string"?query:query.text,args=values??(typeof query==="object"?query.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows??[],rowCount:r?.affectedRows??0};}} as unknown as ClientBase;
 const repo=new EmbeddingBackfillRepository(client as unknown as PoolClient);
 const worker=async<T>(f:()=>Promise<T>)=>{await db.exec("SET ROLE embedding_worker");try{return await f();}finally{await db.exec("RESET ROLE");}};
 const run=(provider:BackfillProvider=fakeProvider(),write=true,patch:Partial<BackfillOptions>={})=>worker(()=>runEmbeddingBackfill({repo,provider,profile,configurationHash:hash,artifactSha256:sha,write,options:options(patch)}));
 const clear=()=>db.exec("TRUNCATE public.product_embeddings,public.category_embeddings");
 try {
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const f of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])await db.exec(await readFile(resolve("tests/fixtures",f),"utf8"));
  for(const f of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql"])await copyFile(resolve("migrations",f),join(dir,f));
  await applyMigrations(client,dir,"up");
  await t.test("register rerun is exact and never overwrites existing ID",async()=>{
   await db.exec("BEGIN");await registerEmbeddingModel(client as unknown as PoolClient,"EM0001",profile,sha);await db.exec("COMMIT");
   await db.exec("BEGIN");await registerEmbeddingModel(client as unknown as PoolClient,"EM0001",profile,sha);await db.exec("COMMIT");
   await db.exec("BEGIN");await assert.rejects(()=>registerEmbeddingModel(client as unknown as PoolClient,"EM0001",profile,"a".repeat(64)),/different/);await db.exec("ROLLBACK");
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM embedding_models")).rows[0]!.n,1);
  });
  await t.test("worker model selection requires exact SHA/profile/active ID",async()=>worker(async()=>{
   await repo.assertModel("EM0001",profile,sha);await assert.rejects(()=>repo.assertModel("EM0001",profile,"a".repeat(64)));await assert.rejects(()=>repo.assertModel("EM9999",profile,sha));
  }));
  await t.test("dry run counts real inspected tokens but creates no jobs/vectors",async()=>{
   const p=fakeProvider(),r=await run(p,false);assert.equal(r.scanned,3);assert.equal(r.total_tokens,18);assert.equal(r.claimed,0);assert.equal(p.calls,0);
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM category_embeddings")).rows[0]!.n,0);
  });
  await t.test("budget stops before claim and cursor preserves unprocessed document",async()=>{
   const p=fakeProvider(),r=await run(p,true,{maxTokens:5});assert.equal(r.stopped,"token_budget");assert.equal(r.next_after,null);assert.equal(r.claimed,0);assert.equal(p.calls,0);
  });
  await t.test("oversize source is reported unchanged with no inference or row writes",async()=>{
   const p=fakeProvider(2049),r=await run(p);assert.equal(r.over_context,3);assert.equal(r.ready,0);assert.equal(p.calls,0);assert.equal(r.max_document_tokens,2049);
  });
  await t.test("smoke writes only bounded category vectors and rerun skips fresh rows",async()=>{
   const a=await run();assert.equal(a.ready,3);const b=await run();assert.equal(b.ready,3);assert.notEqual(a.next_after,b.next_after);assert.equal(b.coverage_after.fresh,6);
  });
  await t.test("published product run and coverage never include draft source",async()=>{
   const r=await run(fakeProvider(),true,{kind:"product",limit:100});assert.equal(r.ready,r.coverage_after.total);assert.ok(r.ready>0 && r.ready<16);
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings e JOIN products p ON p.id=e.product_id WHERE p.status='draft'")).rows[0]!.n,0);
  });
  await t.test("no transaction is held during provider HTTP",async()=>{
   await clear();const p=fakeProvider(),old=p.embed.bind(p);p.embed=async input=>{const r=(await db.query<{x:number}>("SELECT txid_current_if_assigned() AS x")).rows[0];assert.equal(r!.x,null);return old(input);};
   assert.equal((await run(p)).ready,3);
  });
  await t.test("second worker cannot claim an unexpired lease",async()=>{
   await clear();await worker(async()=>{const d=(await repo.page("category",null,1))[0]!;assert.ok(await repo.claim("category",d,"EM0001",3,120));assert.equal(await repo.claim("category",d,"EM0001",3,120),null);});
  });
  await t.test("expired lease can be reclaimed and old completion is rejected",async()=>{
   await clear();const d=(await repo.page("category",null,1))[0]!,old=await worker(()=>repo.claim("category",d,"EM0001",3,120));
   await db.exec("UPDATE category_embeddings SET lease_until=clock_timestamp()-interval '1 second'");
   await worker(async()=>{const next=await repo.claim("category",d,"EM0001",3,120);assert.ok(next);assert.equal(next.attempt,2);assert.equal(await repo.finish("category",old!,"EM0001",{vector:[1,0,0]}),false);assert.equal(await repo.finish("category",next,"EM0001",{vector:[1,0,0]}),true);});
  });
  await t.test("source template edit invalidates claimed completion and permits new source job",async()=>{
   await clear();const d=(await repo.page("category",null,1))[0]!,old=await worker(()=>repo.claim("category",d,"EM0001",3,120));
   await db.query("UPDATE category_search_documents SET template_version='new-v2' WHERE category_id=$1",[d.id]);
   await worker(async()=>{assert.equal(await repo.finish("category",old!,"EM0001",{vector:[1,0,0]}),false);const now=(await repo.page("category",null,1))[0]!;const c=await repo.claim("category",now,"EM0001",3,120);assert.equal(c?.attempt,1);});
  });
  await t.test("transient failure persists retry timestamp; immediate retry is skipped",async()=>{
   await clear();const p=fakeProvider();p.embed=async()=>{throw new EmbeddingError("network_error",3);};const r=await run(p,true,{limit:1});assert.equal(r.failed,1);
   const d=(await repo.page("category",null,1))[0]!;assert.equal(await worker(()=>repo.claim("category",d,"EM0001",3,120)),null);
   const row=(await db.query<{status:string;next_retry_at:unknown}>("SELECT status,next_retry_at FROM category_embeddings")).rows[0]!;assert.equal(row.status,"failed");assert.ok(row.next_retry_at);
  });
  await t.test("terminal invalid response is not retried and stops subsequent inference",async()=>{
   await clear();const p=fakeProvider();p.embed=async()=>{throw new EmbeddingError("invalid_response");};const r=await run(p);assert.equal(r.failed,1);assert.equal(r.stopped,"invalid_response");
   const row=(await db.query<{next_retry_at:null}>("SELECT next_retry_at FROM category_embeddings")).rows[0]!;assert.equal(row.next_retry_at,null);
  });
  await t.test("deactivated model rejects completion and next execution",async()=>{
   await clear();const d=(await repo.page("category",null,1))[0]!,c=await worker(()=>repo.claim("category",d,"EM0001",3,120));await db.exec("UPDATE embedding_models SET is_active=false");
   assert.equal(await worker(()=>repo.finish("category",c!,"EM0001",{vector:[1,0,0]})),false);await assert.rejects(()=>run());await db.exec("UPDATE embedding_models SET is_active=true");
  });
  await t.test("coverage of an inactive model does not count stored ready rows as fresh",async()=>{
   await clear();await run();await db.exec("UPDATE embedding_models SET is_active=false");assert.equal((await repo.coverage("category","EM0001")).fresh,0);await db.exec("UPDATE embedding_models SET is_active=true");
  });
  await t.test("write limits, bad cursor and exhausted retries fail closed",async()=>{
   await assert.rejects(()=>run(fakeProvider(),true,{limit:1001}));assert.throws(()=>validateBackfillOptions(options({after:"drop table"})));
   await clear();const d=(await repo.page("category",null,1))[0]!;await worker(async()=>{for(let i=0;i<3;i++){const c=await repo.claim("category",d,"EM0001",3,120);assert.ok(c);assert.ok(await repo.finish("category",c,"EM0001",{error:"network_error",retrySeconds:0}));}assert.equal(await repo.claim("category",d,"EM0001",3,120),null);});
  });
  await t.test("SIGINT-style cancellation before work writes no rows",async()=>{
   await clear();const p=fakeProvider(),c=new AbortController();c.abort();
   const r=await worker(()=>runEmbeddingBackfill({repo,provider:p,profile,configurationHash:hash,artifactSha256:sha,write:true,options:options(),signal:c.signal}));
   assert.equal(r.stopped,"cancelled");assert.equal(r.claimed,0);assert.equal(p.calls,0);
  });
  await t.test("source hash drift during inference fences old completion",async()=>{
   await clear();const d=(await repo.page("category",null,1))[0]!,c=await worker(()=>repo.claim("category",d,"EM0001",3,120));
   await db.query("UPDATE category_search_documents SET embedding_text='new words',source_hash=encode(extensions.digest('new words','sha256'),'hex') WHERE category_id=$1",[d.id]);
   assert.equal(await worker(()=>repo.finish("category",c!,"EM0001",{vector:[1,0,0]})),false);
  });
  await t.test("invalid source hash is quarantined by report with no inference",async()=>{
   await clear();const page=repo.page.bind(repo);repo.page=async(...args)=>(await page(...args)).map(d=>({...d,hash:"bad"}));
   try{const p=fakeProvider(),r=await run(p);assert.equal(r.invalid_source,3);assert.equal(r.ready,0);assert.equal(p.calls,0);}finally{repo.page=page;}
  });
  await t.test("configuration change after initial selection cannot acquire new job",async()=>{
   await clear();await repo.assertModel("EM0001",profile,sha);
   await db.exec("UPDATE embedding_models SET dimensions=4 WHERE id='EM0001'");
   const d=(await repo.page("category",null,1))[0]!;assert.equal(await worker(()=>repo.claim("category",d,"EM0001",3,120)),null);
   await db.exec("UPDATE embedding_models SET dimensions=3 WHERE id='EM0001'");
  });
  await t.test("product publication changed to draft fences completion through RLS",async()=>{
   await clear();const d=(await repo.page("product",null,1))[0]!,c=await worker(()=>repo.claim("product",d,"EM0001",3,120));
   await db.query("UPDATE products SET status='draft' WHERE id=$1",[Number(d.id)]);
   try{assert.equal(await worker(()=>repo.finish("product",c!,"EM0001",{vector:[1,0,0]})),false);}
   finally{await db.query("UPDATE products SET status='published' WHERE id=$1",[Number(d.id)]);}
  });
  if(native)await t.test("native concurrent sessions race for one job: exactly one lease wins",async()=>{
   await clear();const d=(await repo.page("category",null,1))[0]!,a=new Client(nativeOptions()),b=new Client(nativeOptions());
   try{await a.connect();await b.connect();await a.query("SET ROLE embedding_worker");await b.query("SET ROLE embedding_worker");
    const ra=new EmbeddingBackfillRepository(a),rb=new EmbeddingBackfillRepository(b);await Promise.all([ra.assertModel("EM0001",profile,sha),rb.assertModel("EM0001",profile,sha)]);
    const results=await Promise.all([ra.claim("category",d,"EM0001",3,120),rb.claim("category",d,"EM0001",3,120)]);
    assert.equal(results.filter(Boolean).length,1);
   }finally{await a.end();await b.end();}
  });
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

test("strict backfill CLI is dry-run by default and accepts bounded write flag",()=>{
 const a=backfillArguments(["--kind","product","--model-id","EM0001"]);assert.equal(a.write,false);assert.equal(a.options.limit,20);
 const b=backfillArguments(["--kind","category","--model-id","EM0001","--write"]);assert.equal(b.write,true);assert.equal(b.options.limit,3);
});
for(const args of [["--bogus"],["--write","--write"],["--kind","product","--kind","category"],["--kind","product","--limit","0"],["--kind","product","--limit","3.5"]])test("reject CLI typo/duplicate/invalid numeric: "+args.join(" "),()=>assert.throws(()=>backfillArguments(args)));
test("retry policy is bounded and never retries malformed model/output or missing credentials",()=>{
 assert.equal(retryDelay(new EmbeddingError("network_error"),1,3),30);assert.equal(retryDelay(new EmbeddingError("network_error"),3,3),null);
 for(const code of ["invalid_response","model_mismatch","invalid_configuration","input_too_long"] as const)assert.equal(retryDelay(new EmbeddingError(code),1,3),null);
});
test("real provider inspect uses tokenizer with no embedding call even when over context",async()=>{
 let embeddingCalls=0;const p=createLlamaEmbeddingProvider({profile,baseUrl:"http://127.0.0.1:18081/v1",runtime:{fetch:async url=>{
  let body:unknown;if(String(url).endsWith("/models"))body={data:[{id:profile.model,meta:{n_embd:3,n_ctx:2048}}]};
  else if(String(url).endsWith("/props"))body={build_info:profile.runtimeVersion,model_path:"/fixture/fixture.gguf",default_generation_settings:{n_ctx:2048}};
  else if(String(url).endsWith("/tokenize"))body={tokens:Array(2049).fill(1)};
  else {embeddingCalls++;throw new Error("No inference expected");}
  return new Response(JSON.stringify(body),{headers:{"content-type":"application/json"}});
 }}});
 const r=await p.inspect({texts:["source"],purpose:"document"});assert.deepEqual(r.tokenCounts,[2049]);assert.equal(r.embeddingCalls,0);assert.equal(embeddingCalls,0);
 await assert.rejects(()=>p.embed({texts:["source"],purpose:"document"}),e=>e instanceof EmbeddingError && e.code==="input_too_long");assert.equal(embeddingCalls,0);
});

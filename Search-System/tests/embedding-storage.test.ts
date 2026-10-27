import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {vector} from "@electric-sql/pglite-pgvector";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import {pgcrypto} from "@electric-sql/pglite/contrib/pgcrypto";
import {Client} from "pg";
import type {ClientBase,PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import {inspectEmbeddingStorage} from "../src/repositories/embedding-storage.repository.js";
import {embeddingConfigurationHash} from "../src/config/embedding.config.js";
import type {EmbeddingProfile} from "../src/embeddings/embedding.types.js";
import {readEmbeddingWorkerConfig,embeddingWorkerPoolOptions} from "../src/config/embedding-worker.config.js";
import {assertEmbeddingWorkerIdentity,createEmbeddingWorkerDatabase,readEmbeddingWorkerIdentity,type EmbeddingWorkerIdentity} from "../src/database/embedding-worker.js";

const fixtureProfile:EmbeddingProfile={provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}};

const native=process.env.EMBEDDING_STORAGE_TEST_MODE==="native";
async function fixtureDatabase():Promise<PGlite> {
 if(!native)return new PGlite({extensions:{vector,pg_trgm,pgcrypto}});
 const host=process.env.EMBEDDING_STORAGE_TEST_HOST;
 if(!host || !(host==="127.0.0.1" || host.startsWith("/tmp/") || host.startsWith("/private/tmp/")))throw new Error("Set an explicit isolated native test host/socket");
 const c=new Client({host,port:Number(process.env.EMBEDDING_STORAGE_TEST_PORT??55439),user:"catalog_test_admin",database:"catalog_embedding_test_c19",
  password:process.env.EMBEDDING_STORAGE_TEST_PASSWORD,connectionTimeoutMillis:5000,statement_timeout:10000});
 try {
  await c.connect();const i=(await c.query<{database:string;user:string;version:string;superuser:boolean}>("SELECT current_database() AS database,current_user AS user,current_setting('server_version') AS version,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS superuser")).rows[0]!;
  if(i.database!=="catalog_embedding_test_c19" || i.user!=="catalog_test_admin" || !i.superuser || !/^18\.6(?:\D|$)/.test(i.version))throw new Error("Native tests require the isolated PostgreSQL18.6 test database/admin, never the catalog DB");
 }catch(e){await c.end();throw e;}
 return {async query(sql:string,params?:unknown[]){const r=await c.query(sql,params);return {rows:r.rows};},
  async exec(sql:string){const r=await c.query(sql);return (Array.isArray(r)?r:[r]).map(x=>({rows:x.rows,affectedRows:x.rowCount??0}));},async close(){await c.end();}} as unknown as PGlite;
}
test("reuse real pgvector storage, source freshness and isolated worker grants",async t=>{
 const db=await fixtureDatabase(),directory=await mkdtemp(join(tmpdir(),"embedding-storage-"));
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
  const sql=typeof query==="string"?query:query.text,args=values??(typeof query==="object"?query.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows??[],rowCount:r?.affectedRows??0};
 }} as unknown as ClientBase;
 const worker=async(action:()=>Promise<void>)=>{await db.exec("SET ROLE embedding_worker");try{await action();}finally{await db.exec("RESET ROLE");}};
 const denied=(action:()=>Promise<unknown>)=>assert.rejects(action,e=>typeof e==="object" && e!==null && "code" in e && e.code==="42501");
 const readyProduct=async(id=100000,model="EM0001",v="[1,0,0]",hash?:string)=>db.query(`
  INSERT INTO public.product_embeddings(product_id,model_id,embedding,content_hash,template_version,status,embedded_at)
  SELECT product_id,$2,$3::extensions.vector,coalesce($4,content_hash),template_version,'ready',now() FROM public.product_search_documents WHERE product_id=$1
  ON CONFLICT(product_id,model_id) DO UPDATE SET embedding=excluded.embedding,content_hash=excluded.content_hash,template_version=excluded.template_version,status='ready',embedded_at=now(),error_message=null`,[id,model,v,hash??null]);
 const readyCategory=async(id="TV0108",model="EM0001",v="[1,0,0]")=>db.query(`
  INSERT INTO public.category_embeddings(category_id,model_id,embedding,document_hash,template_version,status,embedded_at)
  SELECT category_id,$2,$3::extensions.vector,source_hash,template_version,'ready',now() FROM public.category_search_documents WHERE category_id=$1
  ON CONFLICT(category_id,model_id) DO UPDATE SET embedding=excluded.embedding,document_hash=excluded.document_hash,template_version=excluded.template_version,status='ready',embedded_at=now(),error_message=null`,[id,model,v]);
 try {
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION vector WITH SCHEMA extensions;CREATE EXTENSION pgcrypto WITH SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const name of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql","embedding-storage.sql"])
   await db.exec(await readFile(resolve("tests/fixtures",name),"utf8"));
  await t.test("administrator bootstrap is valid and rerunnable without passwords",async()=>{
   const name=(await db.query<{name:string}>("SELECT current_database() AS name")).rows[0]!.name;
   const sql=(await readFile(resolve("docs/embedding-worker-roles.sql"),"utf8")).replace("ON DATABASE supabase_db_product","ON DATABASE "+name);
   await db.exec(sql);await db.exec(sql);
   const role=(await db.query<{rolcanlogin:boolean;rolconnlimit:number}>("SELECT rolcanlogin,rolconnlimit FROM pg_roles WHERE rolname='embedding_worker'")).rows[0]!;
   assert.equal(role.rolcanlogin,true);assert.equal(role.rolconnlimit,2);
  });
  const before=(await db.query<{oid:number;relname:string}>("SELECT oid,relname FROM pg_class WHERE oid IN ('public.embedding_models'::regclass,'public.product_embeddings'::regclass,'public.category_embeddings'::regclass)")).rows;
  for(const name of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql","0007_embedding_storage_access.sql"])
   await copyFile(resolve("migrations",name),join(directory,name));
  await t.test("real migration runner applies7 then reruns without duplicate storage",async()=>{
   assert.equal((await applyMigrations(client,directory,"up")).length,7);assert.deepEqual(await applyMigrations(client,directory,"up"),[]);
   const after=(await db.query<{oid:number;relname:string}>("SELECT oid,relname FROM pg_class WHERE oid IN ('public.embedding_models'::regclass,'public.product_embeddings'::regclass,'public.category_embeddings'::regclass)")).rows;
   assert.deepEqual([...after].sort((a,b)=>a.relname.localeCompare(b.relname)),[...before].sort((a,b)=>a.relname.localeCompare(b.relname)));assert.equal((await db.query<{v:string}>("SELECT extversion AS v FROM pg_extension WHERE extname='vector'")).rows[0]!.v,native?"0.8.2":"0.8.1");
  });
  await t.test("storage inspection reports ready schema but no registered model",async()=>{
   const a=await inspectEmbeddingStorage(client as unknown as PoolClient);assert.ok(a.rls_ready && a.leases && a.dimension_unlocked && a.guards_ready && a.model_guard_ready);
   assert.deepEqual(a.models,[]);assert.deepEqual(a.coverage,[]);assert.equal(a.counts.product_embeddings,0);
  });
  await t.test("storage readiness detects disabled RLS even when tables are empty",async()=>{
   await db.exec("ALTER TABLE embedding_models DISABLE ROW LEVEL SECURITY");
   assert.equal((await inspectEmbeddingStorage(client as unknown as PoolClient)).rls_ready,false);
   await db.exec("ALTER TABLE embedding_models ENABLE ROW LEVEL SECURITY");
  });
  await db.query("INSERT INTO embedding_models(id,provider,model_name,dimensions,distance_metric,is_active) VALUES('EM0001','fixture','fixture-model',3,'cosine',true)");
  await db.query("INSERT INTO embedding_models(id,provider,model_name,dimensions,distance_metric,is_active,settings) VALUES('EM0002','llama_cpp','fixture-local',3,'cosine',true,$1)",[
   JSON.stringify({configuration_hash:embeddingConfigurationHash(fixtureProfile),artifact_sha256:"b".repeat(64)})]);
  await t.test("worker reads sources only for published products; source text remains uneditable",async()=>worker(async()=>{
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM products WHERE status='draft'")).rows[0]!.n,0);
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM product_search_documents WHERE product_id=106045")).rows[0]!.n,0);
   await denied(()=>db.exec("UPDATE products SET status='published' WHERE false"));
   await denied(()=>db.exec("UPDATE product_search_documents SET updated_at=now() WHERE false"));
   await denied(()=>db.exec("UPDATE category_search_documents SET updated_at=now() WHERE false"));
   await denied(()=>db.exec("UPDATE embedding_models SET is_active=true WHERE false"));
   await denied(()=>db.exec("INSERT INTO embedding_models VALUES('EM0099','fixture','bad',3,'cosine','{}',true)"));
  }));
  await t.test("identity SQL proves scoped grants with the real active worker role",async()=>worker(async()=>{
   const i=await readEmbeddingWorkerIdentity(client as unknown as PoolClient);
   assert.equal(i.database_user,"embedding_worker");assert.equal(i.reader_member,true);assert.equal(i.can_write_sources,false);
   assert.equal(i.can_write_vectors,true);assert.equal(i.can_delete_vectors,false);assert.equal(i.can_update_keys,false);
   assert.equal(i.owns_catalog,false);assert.equal(i.unexpected_membership,false);assert.equal(i.replication,false);
  }));
  await t.test("worker can write fresh vectors with source locks but no source UPDATE privilege",async()=>worker(async()=>{
   await readyProduct();await readyCategory();
   const privileges=(await db.query<{models:boolean;docs:boolean}>("SELECT has_any_column_privilege(current_user,'embedding_models','UPDATE') AS models,has_any_column_privilege(current_user,'product_search_documents','UPDATE') AS docs")).rows[0]!;
   assert.equal(privileges.models,false);assert.equal(privileges.docs,false);
  }));
  await t.test("dimension width is enforced for both storage types",async()=>worker(async()=>{
   await assert.rejects(()=>readyProduct(100000,"EM0001","[1,0]"),/dimensions mismatch/);
   await assert.rejects(()=>readyCategory("TV0108","EM0001","[1,0]"),/dimension mismatch/);
  }));
  await t.test("zero cosine vectors fail for products and categories",async()=>worker(async()=>{
   await assert.rejects(()=>readyProduct(100000,"EM0001","[0,0,0]"),/cosine vector/);
   await assert.rejects(()=>readyCategory("TV0108","EM0001","[0,0,0]"),/cosine vector/);
  }));
  await t.test("pgvector itself rejects nonfinite components",async()=>worker(async()=>{
   await assert.rejects(()=>readyProduct(100000,"EM0001","[NaN,0,0]"));await assert.rejects(()=>readyProduct(100000,"EM0001","[Infinity,0,0]"));
  }));
  await t.test("local vectors require normalization and artifact/configuration metadata",async()=>worker(async()=>{
   await assert.rejects(()=>readyProduct(100000,"EM0002","[1,1,0]"),/Unverified/);await readyProduct(100000,"EM0002");
  }));
  await t.test("unverified local model cannot be written ready",async()=>{
   await db.exec("INSERT INTO embedding_models VALUES('EM0003','llama_cpp','unverified',3,'cosine','{}',true)");
   await worker(async()=>{await assert.rejects(()=>readyProduct(100000,"EM0003"),/Unverified/);});
  });
  await t.test("inactive model cannot receive new ready vectors",async()=>{
   await db.exec("UPDATE embedding_models SET is_active=false WHERE id='EM0001'");
   await worker(async()=>{await assert.rejects(()=>readyProduct(),/Inactive/);await assert.rejects(()=>readyCategory(),/Inactive/);});
   const a=await inspectEmbeddingStorage(client as unknown as PoolClient);assert.equal(a.coverage.find(x=>x.kind==="product" && x.model_id==="EM0001")!.fresh,0);
   await db.exec("UPDATE embedding_models SET is_active=true WHERE id='EM0001'");
  });
  await t.test("model semantic configuration remains immutable once rows reference it",async()=>{
   await assert.rejects(()=>db.exec("UPDATE embedding_models SET dimensions=4 WHERE id='EM0001'"),/immutable/);
   await assert.rejects(()=>db.exec("UPDATE embedding_models SET settings='{}' WHERE id='EM0002'"),/immutable/);
  });
  await t.test("source hash change stales product vectors and rejects old completion",async()=>{
   const old=(await db.query<{hash:string}>("SELECT content_hash AS hash FROM product_search_documents WHERE product_id=100000")).rows[0]!.hash;
   await db.exec("UPDATE product_search_documents SET embedding_text='updated source',content_hash=encode(sha256(convert_to('updated source','UTF8')),'hex') WHERE product_id=100000");
   assert.equal((await db.query<{s:string}>("SELECT status AS s FROM product_embeddings WHERE product_id=100000 AND model_id='EM0001'")).rows[0]!.s,"stale");
   await worker(async()=>{await assert.rejects(()=>readyProduct(100000,"EM0001","[1,0,0]",old),/stale/);await readyProduct();});
  });
  await t.test("keyword-only edits do not invalidate embedding source",async()=>{
   await db.exec("UPDATE product_search_documents SET keyword_text=keyword_text||' ราคาค้นคำ' WHERE product_id=100000");
   assert.equal((await db.query<{s:string}>("SELECT status s FROM product_embeddings WHERE product_id=100000 AND model_id='EM0001'")).rows[0]!.s,"ready");
  });
  await t.test("template-only change invalidates even when text hash stays the same",async()=>{
   await db.exec("UPDATE product_search_documents SET template_version='fixture-v2' WHERE product_id=100000");
   assert.equal((await db.query<{s:string}>("SELECT status s FROM product_embeddings WHERE product_id=100000 AND model_id='EM0001'")).rows[0]!.s,"stale");
   await worker(async()=>{await readyProduct();});
  });
  await t.test("pending product jobs also become stale and lose lease after source edits",async()=>{
   await worker(async()=>{await db.exec("UPDATE product_embeddings SET status='pending',attempt_count=1,lease_token='11111111-1111-4111-8111-111111111111',lease_until=now()+interval '1 minute' WHERE product_id=100000 AND model_id='EM0002'");});
   await db.exec("UPDATE product_search_documents SET template_version='fixture-v3' WHERE product_id=100000");
   const row=(await db.query<{status:string;lease_token:null}>("SELECT status,lease_token FROM product_embeddings WHERE product_id=100000 AND model_id='EM0002'")).rows[0]!;
   assert.equal(row.status,"stale");assert.equal(row.lease_token,null);
  });
  await t.test("category source edits clear pending lease and stale ready vectors",async()=>{
   await worker(async()=>{await readyCategory("TV0108","EM0002");await db.exec("UPDATE category_embeddings SET status='pending',attempt_count=1,lease_token='11111111-1111-4111-8111-111111111111',lease_until=now()+interval '1 minute' WHERE category_id='TV0108' AND model_id='EM0002'");});
   await db.exec("UPDATE category_search_documents SET embedding_text='new category',source_hash=encode(extensions.digest('new category','sha256'),'hex') WHERE category_id='TV0108'");
   const rows=(await db.query<{status:string;lease_token:null}>("SELECT status,lease_token FROM category_embeddings WHERE category_id='TV0108'")).rows;
   assert.ok(rows.every(r=>r.status==="stale" && r.lease_token===null));
  });
  await t.test("invalid lease pairs/negative attempts cannot be stored",async()=>worker(async()=>{
   await assert.rejects(()=>db.exec("UPDATE product_embeddings SET status='pending',lease_token='11111111-1111-4111-8111-111111111111' WHERE product_id=100000 AND model_id='EM0001'"));
   await assert.rejects(()=>db.exec("UPDATE product_embeddings SET attempt_count=-1 WHERE product_id=100000 AND model_id='EM0001'"));
  }));
  await t.test("ready completion automatically clears pending lease",async()=>worker(async()=>{
   await db.exec("UPDATE product_embeddings SET status='pending',attempt_count=1,lease_token='11111111-1111-4111-8111-111111111111',lease_until=now()+interval '1 minute' WHERE product_id=100000 AND model_id='EM0001'");
   await readyProduct();assert.equal((await db.query<{l:null}>("SELECT lease_token AS l FROM product_embeddings WHERE product_id=100000 AND model_id='EM0001'")).rows[0]!.l,null);
  }));
  await t.test("draft vectors are invisible and cannot be inserted by worker",async()=>{
   await db.exec("INSERT INTO product_embeddings(product_id,model_id,content_hash,template_version,status) SELECT product_id,'EM0001',content_hash,template_version,'pending' FROM product_search_documents WHERE product_id=106045");
   await worker(async()=>{
    assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings WHERE product_id=106045")).rows[0]!.n,0);
    await denied(()=>db.exec("INSERT INTO product_embeddings(product_id,model_id,content_hash,template_version,status) VALUES(106046,'EM0001','x','v','pending')"));
   });
  });
  await t.test("future broad permissive policy does not expose drafts to worker",async()=>{
   await db.exec("CREATE POLICY fixture_broad ON products FOR SELECT TO catalog_embedding_worker USING(true)");
   await worker(async()=>assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM products WHERE status='draft'")).rows[0]!.n,0));
   await db.exec("DROP POLICY fixture_broad ON products");
  });
  await t.test("worker cannot change identity keys, delete or truncate vectors",async()=>worker(async()=>{
   await denied(()=>db.exec("UPDATE product_embeddings SET product_id=product_id WHERE false"));
   await denied(()=>db.exec("UPDATE category_embeddings SET model_id=model_id WHERE false"));
   await denied(()=>db.exec("DELETE FROM product_embeddings WHERE false"));await denied(()=>db.exec("TRUNCATE category_embeddings"));
  }));
  for(const role of ["search_api","anon"])await t.test(role+" cannot directly read embedding storage or invoke trigger bodies",async()=>{
   await db.exec("SET ROLE "+role);try{await denied(()=>db.exec("SELECT * FROM embedding_models"));await denied(()=>db.exec("SELECT * FROM product_embeddings"));await assert.rejects(()=>db.exec("SELECT public.validate_embedding()"));}finally{await db.exec("RESET ROLE");}
  });
  await t.test("worker inspection respects its published scope and reports coverage honestly",async()=>worker(async()=>{
   const a=await inspectEmbeddingStorage(client as unknown as PoolClient,fixtureProfile);assert.ok(a.leases && a.guards_ready);assert.ok(a.counts.product_documents<16);
   assert.deepEqual(a.matching_active_models,["EM0002"]);assert.ok(a.coverage.some(x=>x.total>x.fresh));
  }));
  await t.test("rollback refuses to discard product job progress",async()=>{
   await assert.rejects(()=>applyMigrations(client,directory,"down"),/job progress/);
  });
  await t.test("reviewed rollback/reapply preserves models, vectors and source tables",async()=>{
   await db.exec("UPDATE product_embeddings SET attempt_count=0,next_retry_at=null,lease_token=null,lease_until=null");
   const before=(await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings")).rows[0]!.n;
   await applyMigrations(client,directory,"down");assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM product_embeddings")).rows[0]!.n,before);
   assert.deepEqual(await applyMigrations(client,directory,"up"),["0007_embedding_storage_access"]);
  });
 }finally{await db.close();await rm(directory,{recursive:true,force:true});}
});

const config=()=>({EMBEDDING_WORKER_DATABASE_URL:"postgresql://embedding_worker@127.0.0.1:15432/supabase_db_product",EMBEDDING_WORKER_DATABASE_PASSWORD:"test-only"});
test("worker configuration and pool are independent from API/admin credentials",()=>{
 const c=readEmbeddingWorkerConfig(config());assert.equal(c.port,15432);const p=embeddingWorkerPoolOptions(c);assert.equal(p.max,2);assert.equal(p.user,"embedding_worker");assert.equal(p.ssl,false);assert.ok(p.options?.includes("read_only=off"));
});
for(const url of ["postgresql://antonius@127.0.0.1/supabase_db_product","postgresql://search_api@127.0.0.1/supabase_db_product","postgresql://embedding_worker:p@127.0.0.1/supabase_db_product","postgresql://embedding_worker@remote/supabase_db_product","postgresql://embedding_worker@127.0.0.1/other","postgresql://embedding_worker@127.0.0.1/supabase_db_product?sslmode=disable"])
 test("reject worker URL overrides: "+url,()=>assert.throws(()=>readEmbeddingWorkerConfig({...config(),EMBEDDING_WORKER_DATABASE_URL:url})));
const identity=():EmbeddingWorkerIdentity=>({database:"supabase_db_product",database_user:"embedding_worker",superuser:false,bypass_rls:false,create_role:false,create_database:false,replication:false,unexpected_membership:false,reader_member:true,default_read_only:"off",owns_catalog:false,can_write_sources:false,can_create_public:false,can_write_vectors:true,can_delete_vectors:false,can_update_keys:false});
test("scoped worker identity passes",()=>assert.doesNotThrow(()=>assertEmbeddingWorkerIdentity(identity())));
for(const change of [{database_user:"antonius"},{superuser:true},{bypass_rls:true},{create_role:true},{create_database:true},{replication:true},{unexpected_membership:true},{reader_member:false},{default_read_only:"on"},{owns_catalog:true},{can_write_sources:true},{can_create_public:true},{can_write_vectors:false},{can_delete_vectors:true},{can_update_keys:true}])
 test("unsafe worker identity rejected: "+Object.keys(change)[0],()=>assert.throws(()=>assertEmbeddingWorkerIdentity({...identity(),...change})));
test("unused worker pool closes idempotently without connecting",async()=>{const d=createEmbeddingWorkerDatabase(readEmbeddingWorkerConfig(config()));await Promise.all([d.close(),d.close()]);});

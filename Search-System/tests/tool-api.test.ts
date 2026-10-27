import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm,writeFile} from "node:fs/promises";
import {join,resolve} from "node:path";
import {tmpdir} from "node:os";
import {execFileSync} from "node:child_process";
import {PGlite} from "@electric-sql/pglite";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import type {ClientBase,PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import {buildApp} from "../src/app.js";
import {createPostgresCatalogRepository} from "../src/repositories/postgres-catalog.repository.js";
import {readToolApiConfig} from "../src/config/tool-api.config.js";
import {validateToolSearchResponse} from "../src/validation/tool-search.validation.js";
import {validDetailsResponse} from "../src/schemas/tool-http.schema.js";
import type {CatalogPool} from "../src/types/catalog-read.types.js";
import type {CatalogGateway} from "../src/services/tool-api.service.js";

const token="test-only-caller-token-abcdefghijklmnopqrstuvwxyz1234567890";
const auth={authorization:"Bearer "+token};
test("authenticated Tool API end-to-end with SQL/RLS/real repository",async t=>{
 const db=new PGlite({extensions:{pg_trgm}}),directory=await mkdtemp(join(tmpdir(),"tool-api-"));
 let checkouts=0;
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
  const sql=typeof query==="string"?query:query.text,args=values??(typeof query==="object"?query.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows??[],rowCount:r?.affectedRows??0};
 },release(){}} as unknown as PoolClient;
 const repo=createPostgresCatalogRepository({async connect(){checkouts++;return client;}} as CatalogPool);
 const app=buildApp({database:null,api:{token},catalog:repo,logger:false});
 const search=(payload:unknown,headers:Record<string,string>=auth)=>app.inject({method:"POST",url:"/api/search",headers,payload:payload as object});
 try {
  for(const name of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql","catalog-facts.sql"])
   await db.exec(await readFile(resolve("tests/fixtures",name),"utf8"));
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const name of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql"])
   await copyFile(resolve("migrations",name),join(directory,name));
  await applyMigrations(client as unknown as ClientBase,directory,"up");await db.exec("SET ROLE search_api");
  await t.test("health stays public and contains no database details",async()=>{
   const r=await app.inject({method:"GET",url:"/health"});assert.equal(r.statusCode,200);assert.deepEqual(r.json(),{status:"ok",service:"search-system"});
  });
  for(const value of [undefined,"","Basic abc","Bearer wrong","Bearer "+token+"x","Bearer  "+token,"Bearer "+token+", Bearer "+token])
   await t.test("rejects missing or malformed auth "+String(value)?.slice(0,15),async()=>{
    const before=checkouts;const r=await search({query:"ทีวี"},value===undefined?{}:{authorization:value});
    assert.equal(r.statusCode,401);assert.equal(r.json().status,"unauthorized");assert.equal(checkouts,before);
    assert.equal(r.headers["www-authenticate"],"Bearer");assert.equal(r.headers["cache-control"],"no-store");
   });
  await t.test("Bearer scheme is case-insensitive, token remains case-sensitive",async()=>{
   assert.equal((await search({query:"ทีวี"},{authorization:"bearer "+token})).statusCode,200);
   assert.equal((await search({query:"ทีวี"},{authorization:"Bearer "+token.toUpperCase()})).statusCode,401);
  });
  await t.test("duplicate Authorization headers are rejected",async()=>{
   const before=checkouts;const r=await app.inject({method:"POST",url:"/api/search",headers:{authorization:["Bearer "+token,"Bearer "+token] as unknown as string},payload:{query:"ทีวี"}});
   assert.equal(r.statusCode,401);assert.equal(checkouts,before);
  });
  await t.test("auth runs before parsing malformed JSON",async()=>{
   const r=await app.inject({method:"POST",url:"/api/search",headers:{"content-type":"application/json"},payload:"{ broken"});assert.equal(r.statusCode,401);
  });
  await t.test("old human-name JSON works through the real SQL path",async()=>{
   const r=await search({query:"ทีวี",semantic_query:"ดูหนัง",filters:{category_query:"ทีวี OLED",price:{max:25000},specs:[{attribute_query:"ขนาดหน้าจอ",op:"eq",value:55,unit:"นิ้ว"}]},limit:5});
   assert.equal(r.statusCode,200);const body=r.json();assert.equal(validateToolSearchResponse(body).valid,true);
   assert.equal(body.semantic_available,false);assert.equal(body.results.length,5);assert.equal(body.results[0].product.article_id,100000);
  });
  await t.test("ID path supports category/features/attribute without aliases",async()=>{
   const r=await search({filters:{category_ids:["TV0108"],feature_ids_all:["FT0023"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]},limit:100});
   assert.equal(r.statusCode,200);assert.equal(validateToolSearchResponse(r.json()).valid,true);assert.ok(r.json().results.length);
  });
  await t.test("query-only uses literal category scope and truthful provenance",async()=>{
   const payload={query:"ทีวี",limit:5};const copy=structuredClone(payload);const r=await search(payload);assert.equal(r.statusCode,200);
   assert.equal(r.json().status,"ok");assert.ok(r.json().warnings.some((w:{code:string})=>w.code==="category_inferred_from_query"));
   assert.ok(r.json().applied_filters.some((f:{path:string})=>f.path==="/query"));assert.deepEqual(payload,copy);
  });
  await t.test("free conversation query requests clarification instead of inventing a category",async()=>{
   const r=await search({query:"อยากได้สินค้าสำหรับบ้านที่เหมาะกับฉัน"});assert.equal(r.statusCode,200);assert.equal(r.json().status,"needs_clarification");
   assert.ok(r.json().clarifications.some((c:{path:string})=>c.path==="/query"));
  });
  await t.test("semantic-only asks clarification and never starts semantic search",async()=>{
   const r=await search({semantic_query:"ดูหนังในห้องนอน"});assert.equal(r.json().status,"needs_clarification");assert.ok(!("results" in r.json()));
  });
  await t.test("explicit identity without category requests scope; no broad replacements",async()=>{
   const r=await search({exact:{article_id:100000}});assert.equal(r.json().status,"needs_clarification");
  });
  await t.test("exact identity with scope cannot be replaced even with incompatible price",async()=>{
   const r=await search({exact:{article_id:100000},filters:{category_ids:["TV0108"],price:{max:0}}});
   assert.deepEqual(r.json().results.map((p:{product:{article_id:number}})=>p.product.article_id),[100000]);
  });
  await t.test("unknown ID clarifies; category conflict returns409",async()=>{
   assert.equal((await search({filters:{category_ids:["ZZ9999"]}})).json().status,"needs_clarification");
   const r=await search({filters:{category_query:"ทีวี OLED",category_ids:["TV0109"]}});assert.equal(r.statusCode,409);assert.equal(r.json().status,"constraint_conflict");
  });
  await t.test("no matches and exhausted page are distinct HTTP200 domain outcomes",async()=>{
   const a=await search({exact:{article_id:199999},filters:{category_ids:["TV0108"]}});
   const b=await search({filters:{category_ids:["TV0108"]},page:100,limit:1});assert.equal(a.statusCode,200);assert.equal(a.json().status,"no_matches");
   assert.equal(b.statusCode,200);assert.equal(b.json().status,"ok");assert.equal(b.json().results.length,0);assert.equal(b.json().meta.has_more,false);
  });
  for(const payload of [null,[],{}, {query:"ทีวี",limit:"5"},{query:"ทีวี",unknown:1},{query:"ทีวี",filters:{unexpected:1}},
   {query:"ทีวี",filters:{price:{min:100,max:10}}},{query:"ทีวี",filters:{specs:[{attribute_query:"ขนาด",op:"eq",value:"55",unit:"inch"}]}},
   {query:"ทีวี",filters:{specs:[{attribute_query:"คุณสมบัติ",op:"eq",value:"false",unit:"x"}]}}])
   await t.test("strict request validation "+JSON.stringify(payload),async()=>{
    const before=checkouts;const r=await search(payload);assert.equal(r.statusCode,400);assert.equal(r.json().status,"invalid_request");assert.equal(checkouts,before);
   });
  await t.test("invalid nested property reports original JSON Pointer",async()=>{
   const r=await search({query:"ทีวี",filters:{price:{wrong:1}}});assert.ok(r.json().issues.some((i:{path:string})=>i.path==="/filters/price/wrong"));
  });
  for(const [payload,contentType,status] of [["{ broken","application/json",400],["","application/json",400],["hello","text/plain",400],["hello","application/xml",415],[JSON.stringify({query:"x".repeat(70000)}),"application/json",413]] as const)
   await t.test("parser/size error is sanitized "+status+contentType,async()=>{
    const r=await app.inject({method:"POST",url:"/api/search",headers:{...auth,"content-type":contentType},payload});
    assert.equal(r.statusCode,status);assert.equal(r.json().status,"invalid_request");assert.ok(!r.body.includes("broken"));
   });
  await t.test("querystring cannot bypass the JSON contract",async()=>{
   const r=await app.inject({method:"POST",url:"/api/search?limit=1",headers:auth,payload:{query:"ทีวี"}});assert.equal(r.statusCode,400);
  });
  await t.test("request ID is server-generated, unique and matches header",async()=>{
   const a=await search({query:"ทีวี"},{...auth,"x-request-id":"attacker-id"}),b=await search({query:"ทีวี"});
   assert.notEqual(a.json().request_id,"attacker-id");assert.notEqual(a.json().request_id,b.json().request_id);assert.equal(a.headers["x-request-id"],a.json().request_id);
  });
  await t.test("details require auth and return full structured facts",async()=>{
   assert.equal((await app.inject({method:"GET",url:"/api/products/100000"})).statusCode,401);
   const r=await app.inject({method:"GET",url:"/api/products/100000",headers:auth});assert.equal(r.statusCode,200);
   assert.equal(validDetailsResponse(r.json()),true);assert.equal(r.json().details.content_sections.length,2);assert.ok(r.json().details.product.mock_notice);
  });
  for(const path of ["99999","200000","100000.0","0100000","abc","100000%20"])
   await t.test("rejects invalid Article path "+path,async()=>{
    const before=checkouts;const r=await app.inject({method:"GET",url:"/api/products/"+path,headers:auth});assert.equal(r.statusCode,400);assert.equal(checkouts,before);
   });
  await t.test("draft and missing detail use indistinguishable404",async()=>{
   const a=await app.inject({method:"GET",url:"/api/products/106045",headers:auth}),b=await app.inject({method:"GET",url:"/api/products/199999",headers:auth});
   assert.equal(a.statusCode,404);assert.equal(b.statusCode,404);assert.deepEqual(a.json().issues,b.json().issues);assert.ok(!a.body.includes("SECRET"));
  });
  await t.test("no endpoint lists the entire database and no write route exists",async()=>{
   for(const [method,url] of [["GET","/api/products"],["GET","/api/search"],["DELETE","/api/products/100000"],["HEAD","/api/products/100000"]] as const){
    const r=await app.inject({method,url,headers:auth});assert.equal(r.statusCode,404);
   }
  });
  await t.test("unknown API routes still require auth",async()=>assert.equal((await app.inject({method:"GET",url:"/api/unknown"})).statusCode,401));
 }finally{await app.close();await db.close();await rm(directory,{recursive:true,force:true});}
});

test("API defaults off, rejects missing token, and cannot run against mock catalog implicitly",async()=>{
 assert.equal(readToolApiConfig({}),null);assert.throws(()=>readToolApiConfig({SEARCH_API_ENABLED:"yes"}));
 assert.throws(()=>readToolApiConfig({SEARCH_API_ENABLED:"true"}));assert.throws(()=>readToolApiConfig({SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:"short"}));
 assert.throws(()=>buildApp({database:null,api:{token},logger:false}),/requires PostgreSQL/);
 const app=buildApp({database:null,api:null,logger:false});try{assert.equal((await app.inject({method:"POST",url:"/api/search",payload:{query:"ทีวี"}})).statusCode,404);}finally{await app.close();}
});
for(const code of ["57014","ECONNREFUSED","28P01","CATALOG_DATA_INVALID"])
 test("database failure "+code+" becomes503 without secrets",async()=>{
  const error=Object.assign(new Error("SECRET_PASSWORD postgresql://owner:secret@host"),{code});
  const catalog:CatalogGateway={async search(){throw error;},async findDetails(){throw error;}};
  const app=buildApp({database:null,api:{token},catalog,logger:false});
  try{for(const r of [await app.inject({method:"POST",url:"/api/search",headers:auth,payload:{query:"ทีวี"}}),await app.inject({method:"GET",url:"/api/products/100000",headers:auth})]){
   assert.equal(r.statusCode,503);assert.equal(r.json().status,"search_unavailable");assert.ok(!r.body.includes("secret"));assert.ok(!r.body.includes(code));
  }}finally{await app.close();}
 });

test("private token configuration is ignored, idempotent and never printed",async()=>{
 const dir=await mkdtemp(join(tmpdir(),"tool-token-"));
 try{
  execFileSync("git",["-c","init.defaultBranch=test","init","-q"],{cwd:dir});
  await writeFile(join(dir,".gitignore"),".env\n");await writeFile(join(dir,".env"),"DATA_SOURCE=postgres\nSEARCH_DATABASE_PASSWORD=fake-test-secret\n");
  const configure=()=>execFileSync(process.execPath,["--import",import.meta.resolve("tsx"),resolve("src/scripts/configure-tool-api.ts")],{cwd:dir,encoding:"utf8"});
  const first=configure(),one=await readFile(join(dir,".env"),"utf8"),second=configure(),two=await readFile(join(dir,".env"),"utf8");
  assert.equal(one,two);assert.ok(one.includes("SEARCH_DATABASE_PASSWORD=fake-test-secret"));
  const secret=one.match(/^SEARCH_API_TOKEN=(.+)$/m)![1]!;assert.match(secret,/^[A-Za-z0-9_-]{43}$/);
  assert.ok(!first.includes(secret) && !second.includes(secret) && !first.includes("fake-test-secret"));
  assert.equal(JSON.parse(second).existing_token_preserved,true);
 }finally{await rm(dir,{recursive:true,force:true});}
});

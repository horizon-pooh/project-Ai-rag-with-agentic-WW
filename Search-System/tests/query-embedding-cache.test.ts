import assert from "node:assert/strict";
import {test} from "node:test";
import {createQueryEmbeddingCache,type QueryCacheIdentity} from "../src/embeddings/query-embedding-cache.js";
import {DEFAULT_QUERY_CACHE_CONFIG,readQueryCacheConfig,type QueryCacheConfig} from "../src/config/query-cache.config.js";
import {embeddingConfigurationHash} from "../src/config/embedding.config.js";
import {EmbeddingError,type EmbeddingProfile,type EmbeddingResult} from "../src/embeddings/embedding.types.js";
import type {QueryEmbeddingProvider} from "../src/services/category-semantic.service.js";
const profile=():EmbeddingProfile=>({provider:"llama_cpp",model:"fixture-local",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",query:{prefix:""},document:{prefix:""}});
const identity=(patch:Partial<QueryCacheIdentity>={}):QueryCacheIdentity=>{const p=profile();return {modelId:"EM0001",profile:p,configurationHash:embeddingConfigurationHash(p),artifactSha256:"b".repeat(64),baseUrl:"http://127.0.0.1:18081/v1",...patch};};
const result=(i=identity()):EmbeddingResult=>({provider:"llama_cpp",model:i.profile.model,dimensions:i.profile.dimensions,configurationHash:i.configurationHash,purpose:"query",vectors:[[1,0,0]],usage:{promptTokens:4,totalTokens:4},attempts:1,tokenCounts:[4]});
const bounds=(patch:Partial<QueryCacheConfig>={}):QueryCacheConfig=>({...DEFAULT_QUERY_CACHE_CONFIG,...patch});
const input=(text="ทีวี OLED",signal?:AbortSignal)=>({texts:[text],purpose:"query" as const,...(signal?{signal}:{})});
const tick=()=>new Promise<void>(r=>setImmediate(r));
function deferred<T>(){let resolve!:(v:T)=>void,reject!:(e:unknown)=>void;const promise=new Promise<T>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function source(i=identity()){const p:QueryEmbeddingProvider & {calls:number;seen:string[]}={calls:0,seen:[],async embed(x){p.calls++;p.seen.push(...x.texts);return result(i);}};return p;}
const is=(code:string)=>(e:unknown)=>e instanceof EmbeddingError && e.code===code;

test("คำเดิมใช้ hit คืนสำเนา และไม่อ้างว่าเรียกโมเดลอีก",async()=>{
 const c=createQueryEmbeddingCache(),p=source(),w=c.wrap(p,identity());try{const a=await w.embed(input());assert.equal(a.cache_status,"miss");assert.equal(a.attempts,1);a.vectors[0]![0]=0;a.tokenCounts[0]=999;
  const b=await w.embed(input());assert.equal(b.cache_status,"hit");assert.deepEqual(b.vectors,[[1,0,0]]);assert.deepEqual(b.tokenCounts,[4]);assert.equal(b.attempts,0);assert.deepEqual(b.usage,{promptTokens:0,totalTokens:0});assert.equal(p.calls,1);assert.equal(c.stats().hits,1);
 }finally{await c.close();}
});
test("TTL นับจากตอนเก็บ ไม่ต่ออายุทุกครั้งที่ hit",async()=>{
 let time=0;const c=createQueryEmbeddingCache(bounds({ttlMs:100}),{now:()=>time}),p=source(),w=c.wrap(p,identity());try{await w.embed(input());time=99;assert.equal((await w.embed(input())).cache_status,"hit");time=100;assert.equal((await w.embed(input())).cache_status,"miss");assert.equal(p.calls,2);}finally{await c.close();}
});
test("LRU ไล่รายการที่ไม่ถูกใช้และจำกัดจำนวน",async()=>{
 const c=createQueryEmbeddingCache(bounds({maxEntries:2})),p=source(),w=c.wrap(p,identity());try{await w.embed(input("A"));await w.embed(input("B"));await w.embed(input("A"));await w.embed(input("C"));assert.equal(c.stats().entries,2);assert.equal((await w.embed(input("B"))).cache_status,"miss");assert.equal(p.calls,4);assert.ok(c.stats().evictions>=1);}finally{await c.close();}
});
test("งบ byte จำกัดข้อมูลแม้จำนวนรายการยังไม่ถึงเพดาน",async()=>{
 const c=createQueryEmbeddingCache(bounds({maxBytes:3000,maxEntries:100})),p=source(),w=c.wrap(p,identity());try{await w.embed(input("A"));await w.embed(input("B"));assert.equal(c.stats().entries,1);assert.ok(c.stats().bytes<=3000);assert.equal((await w.embed(input("A"))).cache_status,"miss");}finally{await c.close();}
});
test("รายการใหญ่เกินงบยังตอบได้แต่ไม่เก็บ",async()=>{
 const c=createQueryEmbeddingCache(bounds({maxBytes:1024})),p=source(),w=c.wrap(p,identity());try{await w.embed(input());await w.embed(input());assert.equal(p.calls,2);assert.equal(c.stats().entries,0);assert.equal(c.stats().bytes,0);}finally{await c.close();}
});
test("profile รุ่น model ID artifact URL และ credential แยก namespace",async()=>{
 const c=createQueryEmbeddingCache();try{
  const variants=[identity(),identity({modelId:"EM0002"}),identity({artifactSha256:"c".repeat(64)}),identity({baseUrl:"http://127.0.0.1:18082/v1"}),identity({apiKey:"fixture-key"})];
  const changed=profile();changed.query.prefix="instruction:";changed.version="fixture-v2";variants.push(identity({profile:changed,configurationHash:embeddingConfigurationHash(changed)}));
  for(const i of variants){const p=source(i);assert.equal((await c.wrap(p,i).embed(input())).cache_status,"miss");assert.equal(p.calls,1);}
  assert.equal(c.stats().entries,variants.length);
 }finally{await c.close();}
});
test("ไม่ normalize ข้อความเพื่อรวมคำที่ต่างกันเอง",async()=>{
 const c=createQueryEmbeddingCache(),p=source(),w=c.wrap(p,identity());try{for(const q of ["TV","tv"," TV","TV "])assert.equal((await w.embed(input(q))).cache_status,"miss");assert.equal(p.calls,4);}finally{await c.close();}
});
test("จับข้อความและ identity ก่อน await ไม่รับการแก้ input ทีหลัง",async()=>{
 const c=createQueryEmbeddingCache(),i=identity(),p=source(),w=c.wrap(p,i),x=input("ต้นฉบับ");try{const running=w.embed(x);x.texts[0]="เปลี่ยน";i.profile.model="changed";await running;assert.deepEqual(p.seen,["ต้นฉบับ"]);}finally{await c.close();}
});
test("ไม่เก็บ property ส่วนเกินจาก provider และ stats ไม่มีข้อความหรือ key",async()=>{
 const c=createQueryEmbeddingCache(),p=source();p.embed=async()=>({...result(),raw_text:"secret-query",private_key:"secret-key",usage:{promptTokens:4,totalTokens:4,raw_text:"secret-nested"}} as EmbeddingResult);try{const w=c.wrap(p,identity({apiKey:"secret-key"})),r=await w.embed(input("secret-query"));assert.ok(!JSON.stringify(r).includes("secret-"));assert.ok(!JSON.stringify(c.stats()).includes("secret-"));assert.ok(Object.values(c.stats()).every(v=>typeof v==="number"));}finally{await c.close();}
});
test("single-flight รวมคำซ้ำขณะรอ และนับ usage เฉพาะเจ้าของงาน",async()=>{
 const c=createQueryEmbeddingCache(),d=deferred<EmbeddingResult>();let calls=0;
 const w=c.wrap({async embed(){calls++;return d.promise;}},identity());try{const all=[w.embed(input()),w.embed(input()),w.embed(input())];await tick();assert.equal(calls,1);assert.equal(c.stats().shared,2);d.resolve(result());const [a,b,e]=await Promise.all(all);assert.equal(a!.cache_status,"miss");assert.equal(b!.cache_status,"shared");assert.equal(e!.cache_status,"shared");assert.equal(b!.attempts,0);assert.equal(b!.usage.totalTokens,0);a!.vectors[0]![0]=0;assert.equal(b!.vectors[0]![0],1);assert.equal(c.stats().in_flight,0);}finally{await c.close();}
});
test("ยกเลิกคนเดียวไม่ยกเลิกงานที่คนอื่นรอ",async()=>{
 const c=createQueryEmbeddingCache(),d=deferred<EmbeddingResult>(),a=new AbortController();let signal:AbortSignal|undefined;
 const w=c.wrap({async embed(x){signal=x.signal;return d.promise;}},identity());try{const first=w.embed(input("same",a.signal)),cancelled=assert.rejects(first,is("cancelled")),second=w.embed(input("same"));await tick();a.abort();await cancelled;assert.equal(signal?.aborted,false);d.resolve(result());assert.equal((await second).cache_status,"shared");assert.equal(c.stats().entries,1);}finally{await c.close();}
});
test("ยกเลิกทุกคนแล้วผลที่มาช้าไม่เข้า cache",async()=>{
 const c=createQueryEmbeddingCache(),d=deferred<EmbeddingResult>(),a=new AbortController(),b=new AbortController();let signal:AbortSignal|undefined;
 const w=c.wrap({async embed(x){signal=x.signal;return d.promise;}},identity());try{const pa=assert.rejects(w.embed(input("same",a.signal)),is("cancelled")),pb=assert.rejects(w.embed(input("same",b.signal)),is("cancelled"));await tick();a.abort();b.abort();await Promise.all([pa,pb]);assert.equal(signal?.aborted,true);d.resolve(result());await tick();assert.equal(c.stats().entries,0);assert.equal(c.stats().pending_queries,0);}finally{await c.close();}
});
test("จำกัดงานคนละคำที่ยังค้างและไม่เปิดคิวไม่จำกัด",async()=>{
 const c=createQueryEmbeddingCache(bounds({maxInFlight:1})),d=deferred<EmbeddingResult>();let calls=0;
 const w=c.wrap({async embed(){calls++;return d.promise;}},identity());try{const a=w.embed(input("A"));await tick();await assert.rejects(()=>w.embed(input("B")),is("capacity_exceeded"));assert.equal(calls,1);d.resolve(result());await a;assert.equal(c.stats().rejected,1);}finally{await c.close();}
});
test("จำกัดจำนวนผู้รอคำเดียวกัน",async()=>{
 const c=createQueryEmbeddingCache(bounds({maxWaiters:2})),d=deferred<EmbeddingResult>(),w=c.wrap({async embed(){return d.promise;}},identity());try{const a=w.embed(input()),b=w.embed(input());await assert.rejects(()=>w.embed(input()),is("capacity_exceeded"));d.resolve(result());await Promise.all([a,b]);assert.equal(c.stats().shared,1);}finally{await c.close();}
});
test("provider ไม่ตอบก็ timeout โดยยังกัน slot จนงานจริงจบ",async()=>{
 const c=createQueryEmbeddingCache(bounds({timeoutMs:20,maxInFlight:1})),d=deferred<EmbeddingResult>();let healthy=false,signal:AbortSignal|undefined;
 const w=c.wrap({async embed(x){signal=x.signal;return healthy?result():d.promise;}},identity());try{await assert.rejects(()=>w.embed(input()),is("timeout"));assert.equal(signal?.aborted,true);assert.equal(c.stats().pending_queries,0);assert.equal(c.stats().entries,0);assert.equal(c.stats().in_flight,1);await assert.rejects(()=>w.embed(input("another")),is("capacity_exceeded"));healthy=true;d.resolve(result());await tick();assert.equal(c.stats().in_flight,0);assert.equal((await w.embed(input())).cache_status,"miss");assert.equal((await w.embed(input())).cache_status,"hit");}finally{await c.close();}
});
test("ไม่ cache ความล้มเหลวและลองใหม่ได้เมื่อ provider กลับมา",async()=>{
 const c=createQueryEmbeddingCache();let calls=0;const w=c.wrap({async embed(){calls++;if(calls===1)throw new EmbeddingError("network_error");return result();}},identity());try{await assert.rejects(()=>w.embed(input()),is("network_error"));assert.equal(c.stats().entries,0);assert.equal((await w.embed(input())).cache_status,"miss");assert.equal((await w.embed(input())).cache_status,"hit");assert.equal(calls,2);}finally{await c.close();}
});
const bad:Array<[string,(r:EmbeddingResult)=>EmbeddingResult]>=[
 ["hash",r=>({...r,configurationHash:"a".repeat(64)})],["model",r=>({...r,model:"other"})],["dimensions",r=>({...r,dimensions:4})],["purpose",r=>({...r,purpose:"document"})],
 ["zero",r=>({...r,vectors:[[0,0,0]]})],["norm",r=>({...r,vectors:[[1,1,0]]})],["NaN",r=>({...r,vectors:[[NaN,0,0]]})],["float32",r=>({...r,vectors:[[1e40,0,0]]})],
 ["count",r=>({...r,vectors:[]})],["tokens",r=>({...r,tokenCounts:[2049]})],["usage",r=>({...r,usage:{promptTokens:5,totalTokens:5}})]
];
for(const [name,change] of bad)test("ผล provider ผิด "+name+" ไม่ถูกเก็บ",async()=>{
 const c=createQueryEmbeddingCache();let calls=0;const w=c.wrap({async embed(){calls++;return calls===1?change(result()):result();}},identity());try{await assert.rejects(()=>w.embed(input()),is("invalid_response"));assert.equal(c.stats().entries,0);await w.embed(input());await w.embed(input());assert.equal(calls,2);}finally{await c.close();}
});
test("ปิด cache แล้วไม่เก็บหรือ share แต่ยังจำกัดงาน",async()=>{
 const c=createQueryEmbeddingCache(bounds({enabled:false})),p=source(),w=c.wrap(p,identity());try{assert.equal((await w.embed(input())).cache_status,"disabled");assert.equal((await w.embed(input())).cache_status,"disabled");assert.equal(p.calls,2);assert.equal(c.stats().entries,0);assert.equal(c.stats().hits,0);}finally{await c.close();}
});
test("ตรวจ purpose batch และ byte ก่อนอ่าน cache",async()=>{
 const c=createQueryEmbeddingCache(),p=source(),w=c.wrap(p,identity());try{await w.embed(input("12345"));await assert.rejects(()=>c.wrap(p,identity({maxInputBytes:4})).embed(input("12345")),is("invalid_input"));await assert.rejects(()=>w.embed({texts:["x","y"],purpose:"query"}),is("invalid_input"));await assert.rejects(()=>w.embed({texts:["x"],purpose:"document" as "query"}),is("invalid_input"));assert.equal(p.calls,1);}finally{await c.close();}
});
test("signal ที่ยกเลิกแล้วไม่เริ่ม provider",async()=>{
 const c=createQueryEmbeddingCache(),p=source(),a=new AbortController();a.abort();try{await assert.rejects(()=>c.wrap(p,identity()).embed(input("x",a.signal)),is("cancelled"));assert.equal(p.calls,0);}finally{await c.close();}
});
test("close ยกเลิกงาน ล้างข้อมูล และเรียกซ้ำได้",async()=>{
 const c=createQueryEmbeddingCache(),d=deferred<EmbeddingResult>(),w=c.wrap({async embed(){return d.promise;}},identity());const p=assert.rejects(w.embed(input()),is("cancelled"));await tick();await Promise.all([c.close(),c.close()]);await p;assert.equal(c.stats().entries,0);assert.equal(c.stats().in_flight,0);await assert.rejects(()=>w.embed(input()),is("cancelled"));d.resolve(result());
});
test("ค่าตั้ง cache มีขอบเขตและปิดได้",()=>{assert.equal(readQueryCacheConfig({}).maxEntries,128);assert.equal(readQueryCacheConfig({SEARCH_QUERY_CACHE_ENABLED:"false"}).enabled,false);assert.equal(readQueryCacheConfig({SEARCH_QUERY_CACHE_TTL_MS:"10"}).ttlMs,10);});
for(const env of [{SEARCH_QUERY_CACHE_ENABLED:"yes"},{SEARCH_QUERY_CACHE_MAX_ENTRIES:"0"},{SEARCH_QUERY_CACHE_TTL_MS:"1.5"},{SEARCH_QUERY_CACHE_MAX_BYTES:"999999999"},{SEARCH_QUERY_CACHE_MAX_IN_FLIGHT:"17"}])test("ปฏิเสธค่าตั้งไม่ถูกต้อง "+Object.keys(env)[0],()=>assert.throws(()=>readQueryCacheConfig(env)));

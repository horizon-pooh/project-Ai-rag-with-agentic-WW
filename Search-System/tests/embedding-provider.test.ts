import assert from "node:assert/strict";
import {test} from "node:test";
import {mkdtemp,writeFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {existsSync} from "node:fs";
import {execFileSync} from "node:child_process";
import {createLlamaEmbeddingProvider,type EmbeddingRuntime} from "../src/embeddings/llama-embedding.provider.js";
import {EmbeddingError,type EmbeddingProfile,type EmbeddingBounds} from "../src/embeddings/embedding.types.js";
import {DEFAULT_EMBEDDING_BOUNDS,embeddingConfigurationHash,readEmbeddingConfiguration} from "../src/config/embedding.config.js";

const profile=():EmbeddingProfile=>({provider:"llama_cpp",model:"test/embedding-fixture",dimensions:3,distanceMetric:"cosine",version:"fixture-v1",pooling:"last",normalization:"server_l2",contextTokens:2048,runtimeVersion:"fixture-build",artifact:"fixture.gguf",
 query:{prefix:""},document:{prefix:""}});
const key="test-only-key-NEVER-PRINT-IN-ERROR";
const success=(vectors:number[][]=[[1,0,0]],model="test/embedding-fixture")=>({object:"list",model,
 data:vectors.map((embedding,index)=>({object:"embedding",index,embedding})),usage:{prompt_tokens:vectors.length*6,total_tokens:vectors.length*6}});
const response=(value:unknown,status=200,headers:Record<string,string>={})=>new Response(JSON.stringify(value),{status,headers:{"content-type":"application/json",...headers}});
function harness(sequence:Array<Response|Error|((init:RequestInit)=>Promise<Response>)>,p=profile(),b:Partial<EmbeddingBounds>={}) {
 const serving=structuredClone(p);let time=0;const calls:Array<{url:string;init:RequestInit}>=[],waits:number[]=[];
 const allCalls:Array<{url:string;init:RequestInit}>=[];
 const fetcher:typeof fetch=async(url,init)=>{
  allCalls.push({url:String(url),init:init!});
  if(String(url).endsWith("/models"))return response({data:[{id:serving.model,meta:{n_embd:serving.dimensions,n_ctx:serving.contextTokens}}]});
  if(String(url).endsWith("/props"))return response({build_info:serving.runtimeVersion,model_path:"/fixture/"+serving.artifact,default_generation_settings:{n_ctx:serving.contextTokens}});
  if(String(url).endsWith("/tokenize"))return response({tokens:[1,2,3,4,5,6]});
  calls.push({url:String(url),init:init!});const item=sequence.shift();if(item instanceof Error)throw item;
  if(typeof item==="function")return item(init!);if(!item)throw new Error("No transport fixture");return item;
 };
 const runtime:Partial<EmbeddingRuntime>={fetch:fetcher,now:()=>time,random:()=>1,sleep:async ms=>{waits.push(ms);time+=ms;}};
 return {provider:createLlamaEmbeddingProvider({profile:p,baseUrl:"http://127.0.0.1:18081/v1",apiKey:key,bounds:{...DEFAULT_EMBEDDING_BOUNDS,...b},runtime}),calls,allCalls,waits,advance:(ms:number)=>{time+=ms;}};
}
const failure=(code:string,attempts?:number)=> (e:unknown)=> e instanceof EmbeddingError && e.code===code && (attempts===undefined || e.attempts===attempts);

test("sends loopback JSON and reorders indexed results without mutating texts",async()=>{
 const body=success([[1,0,0],[0,1,0]]);body.data.reverse();const h=harness([response(body)]);
 const texts=["ทีวี OLED 55 นิ้ว","  preserve SPACES\nภาษาไทย  "];const copy=[...texts];
 const r=await h.provider.embed({texts,purpose:"document"});assert.deepEqual(texts,copy);assert.equal(r.attempts,1);
 assert.deepEqual(r.vectors,[[1,0,0],[0,1,0]]);assert.deepEqual(r.usage,{promptTokens:12,totalTokens:12});
 assert.equal(h.calls[0]!.url,"http://127.0.0.1:18081/v1/embeddings");assert.equal(h.calls[0]!.init.redirect,"error");
 assert.deepEqual(JSON.parse(String(h.calls[0]!.init.body)),{model:"test/embedding-fixture",input:texts,encoding_format:"float"});
 assert.equal((h.calls[0]!.init.headers as Record<string,string>).authorization,"Bearer "+key);
});
test("query/document prefixes are explicit and no provider task/dimensions are sent",async()=>{
 const p=profile();p.query={prefix:"Instruct: Find products\nQuery:"};p.document={prefix:""};
 const h=harness([response(success()),response(success())],p);
 await h.provider.embed({texts:["ทีวี"],purpose:"query"});await h.provider.embed({texts:["ทีวี"],purpose:"document"});
 const q=JSON.parse(String(h.calls[0]!.init.body)),d=JSON.parse(String(h.calls[1]!.init.body));
 assert.equal(q.input[0],"Instruct: Find products\nQuery:ทีวี");assert.equal(d.input[0],"ทีวี");
 assert.ok(!("input_type" in q) && !("dimensions" in q));assert.notEqual(embeddingConfigurationHash(p),embeddingConfigurationHash(profile()));
});
test("provider clones profile and captures request texts/purpose before awaiting transport",async()=>{
 const p=profile();const h=harness([response(success())],p);p.model="changed/model";
 const input={texts:["ต้นฉบับ"],purpose:"document" as const};const pending=h.provider.embed(input);input.texts[0]="changed";
 const r=await pending;assert.equal(r.model,"test/embedding-fixture");assert.equal(JSON.parse(String(h.calls[0]!.init.body)).input[0],"ต้นฉบับ");
});
for(const texts of [[],[""],["  "],["\n"],[123],[null],Array(17).fill("x"),new Array(1)])
 test("rejects invalid batch before network: "+JSON.stringify(texts),async()=>{
  const h=harness([]);await assert.rejects(()=>h.provider.embed({texts:texts as string[],purpose:"document"}),failure("invalid_input",0));assert.equal(h.calls.length,0);
 });
test("UTF8/prefix/total byte bounds reject oversize without truncation",async()=>{
 const p=profile();p.document.prefix="123";const h=harness([],p,{maxInputBytes:8,maxBatchBytes:10});
 await assert.rejects(()=>h.provider.embed({texts:["ภาษา"],purpose:"document"}),failure("invalid_input"));
 await assert.rejects(()=>h.provider.embed({texts:["abc","abc"],purpose:"document"}),failure("invalid_input"));assert.equal(h.calls.length,0);
});
const mutations:Array<[string,(body:ReturnType<typeof success>)=>unknown]>= [
 ["wrong model",b=>({...b,model:"other/model"})],["missing model",b=>{const {model:_,...r}=b;return r;}],
 ["embedded error",b=>({...b,error:{code:429,message:key}})],["wrong envelope object",b=>({...b,object:"embedding"})],
 ["empty data",b=>({...b,data:[]})],["duplicate data",b=>({...b,data:[b.data[0],b.data[0]]})],
 ["missing vector",b=>({...b,data:[{object:"embedding",index:0}]})],
 ["wrong row object",b=>({...b,data:[{...b.data[0],object:"other"}]})],
 ["negative index",b=>({...b,data:[{...b.data[0],index:-1}]})],["index outside batch",b=>({...b,data:[{...b.data[0],index:1}]})],
 ["string index",b=>({...b,data:[{...b.data[0],index:"0"}]})],["fractional index",b=>({...b,data:[{...b.data[0],index:0.5}]})],
 ["wrong dimension",b=>({...b,data:[{...b.data[0],embedding:[1,2]}]})],["base64 instead of float",b=>({...b,data:[{...b.data[0],embedding:"AQID"}]})],
 ["string float",b=>({...b,data:[{...b.data[0],embedding:[1,"2",3]}]})],["null float",b=>({...b,data:[{...b.data[0],embedding:[1,null,3]}]})],
 ["zero norm",b=>({...b,data:[{...b.data[0],embedding:[0,0,0]}]})],["float32 overflow",b=>({...b,data:[{...b.data[0],embedding:[1e40,1,1]}]})],
 ["float32 all underflow",b=>({...b,data:[{...b.data[0],embedding:[1e-100,0,0]}]})],
 ["missing usage",b=>({...b,usage:null})],["string usage",b=>({...b,usage:{prompt_tokens:"12",total_tokens:12}})],
 ["negative usage",b=>({...b,usage:{prompt_tokens:-1,total_tokens:12}})],["inverted usage",b=>({...b,usage:{prompt_tokens:12,total_tokens:1}})],
 ["unsafe usage integer",b=>({...b,usage:{prompt_tokens:12,total_tokens:1e20}})],
];
for(const [name,change] of mutations)test("rejects malformed provider response without retry: "+name,async()=>{
 const h=harness([response(change(success()))]);await assert.rejects(()=>h.provider.embed({texts:["ทีวี"],purpose:"document"}),failure("invalid_response",1));assert.equal(h.calls.length,1);
});
test("duplicate indexes with correct row count are rejected",async()=>{
 const value=success([[1,0,0],[0,1,0]]);value.data[1]!.index=0;const h=harness([response(value)]);
 await assert.rejects(()=>h.provider.embed({texts:["a","b"],purpose:"query"}),failure("invalid_response"));
});
for(const raw of ["{invalid",'{"object":"list","model":"test/embedding-fixture","data":[{"object":"embedding","index":0,"embedding":[1e999,1,1]}],"usage":{"prompt_tokens":1,"total_tokens":1}}'])
 test("invalid JSON or nonfinite exponent cannot be accepted",async()=>{
  const h=harness([new Response(raw,{headers:{"content-type":"application/json"}})]);
  await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"query"}),failure("invalid_response",1));
 });
test("content type and declared/streamed response size are bounded",async()=>{
 for(const res of [new Response("<html>secret</html>",{headers:{"content-type":"text/html"}}),response(success(),200,{"content-length":"99999999"}),new Response(" ".repeat(2000),{headers:{"content-type":"application/json"}})]){
  const h=harness([res],profile(),{maxResponseBytes:1024});await assert.rejects(()=>h.provider.embed({texts:["x"],purpose:"query"}),e=>e instanceof EmbeddingError && ["invalid_response","response_too_large"].includes(e.code));
 }
});
for(const status of [400,401,402,403,404,413,422])test("permanent HTTP"+status+" never retries or leaks provider text",async()=>{
 const h=harness([response({error:{message:key+" PRIVATE PRODUCT TEXT"}},status)]);
 await assert.rejects(()=>h.provider.embed({texts:["PRIVATE PRODUCT TEXT"],purpose:"document"}),e=>{
  assert.ok(e instanceof EmbeddingError);assert.equal(e.code,"http_error");assert.equal(e.httpStatus,status);assert.equal(e.attempts,1);
  assert.ok(!JSON.stringify(e).includes(key));assert.ok(!e.message.includes("PRIVATE"));assert.ok(!("cause" in e));return true;
 });assert.equal(h.calls.length,1);
});
for(const status of [408,429,500,502,503,504,524,529])test("transient HTTP"+status+" retries with bounded backoff",async()=>{
 const h=harness([response({},status),response(success())]);const r=await h.provider.embed({texts:["a"],purpose:"document"});assert.equal(r.attempts,2);assert.deepEqual(h.waits,[250]);
});
test("network exceptions are sanitized and retried, then exhaustion preserves sent-attempt count",async()=>{
 const h=harness([new Error(key),new Error(key),new Error(key)]);await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"document"}),failure("network_error",3));
 assert.equal(h.calls.length,3);assert.deepEqual(h.waits,[250,500]);
});
test("Retry-After seconds are honored",async()=>{
 const h=harness([response({},429,{"retry-after":"2"}),response(success())]);await h.provider.embed({texts:["a"],purpose:"query"});assert.deepEqual(h.waits,[2000]);
});
test("Retry-After date is honored within retry cap",async()=>{
 const date=new Date(Date.now()+3000).toUTCString();const h=harness([response({},503,{"retry-after":date}),response(success())]);
 await h.provider.embed({texts:["a"],purpose:"query"});assert.ok(h.waits[0]!>=1500 && h.waits[0]!<=3000);
});
for(const value of ["9999","9".repeat(500)])test("oversized Retry-After stops instead of retrying early",async()=>{
 const h=harness([response({},429,{"retry-after":value})]);await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"query"}),failure("retry_after_exceeds_budget",1));assert.equal(h.calls.length,1);assert.deepEqual(h.waits,[]);
});
test("retry does not start after total deadline",async()=>{
 const h=harness([async()=>{h.advance(30000);return response({},503);}]);await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"query"}),failure("deadline_exceeded",1));assert.equal(h.calls.length,1);
});
test("late success is rejected past the total deadline",async()=>{
 const h=harness([async()=>{h.advance(30000);return response(success());}]);await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"query"}),failure("deadline_exceeded",1));
});
test("timeout aborts hung transport even when stub ignores cancellation",async()=>{
 const h=harness([async()=>new Promise(()=>{})],profile(),{maxAttempts:1,attemptTimeoutMs:20,totalTimeoutMs:100});
 await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"document"}),failure("timeout",1));assert.equal(h.calls[0]!.init.signal!.aborted,true);
});
test("timeout also covers a response body that never finishes",async()=>{
 const body=new ReadableStream<Uint8Array>({start(c){c.enqueue(new TextEncoder().encode('{"object":'));}});
 const h=harness([new Response(body,{headers:{"content-type":"application/json"}})],profile(),{maxAttempts:1,attemptTimeoutMs:20,totalTimeoutMs:100});
 await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"document"}),failure("timeout",1));
});
test("external cancellation before a request sends nothing",async()=>{
 const h=harness([]),controller=new AbortController();controller.abort(key);
 await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"query",signal:controller.signal}),failure("cancelled",0));assert.equal(h.calls.length,0);
});
test("external cancellation during transport is not retried",async()=>{
 const h=harness([async()=>new Promise(()=>{})]),controller=new AbortController();
 const pending=h.provider.embed({texts:["a"],purpose:"query",signal:controller.signal});controller.abort(key);
 await assert.rejects(()=>pending,failure("cancelled",1));assert.equal(h.allCalls.length,1);assert.equal(h.calls.length,0);
});
test("cancellation during backoff preserves attempt count and stops retries",async()=>{
 const controller=new AbortController();let calls=0;
 const p=createLlamaEmbeddingProvider({profile:profile(),baseUrl:"http://127.0.0.1:18081/v1",apiKey:key,runtime:{fetch:async(url,init)=>{if(!String(url).endsWith("/embeddings"))return preflight(String(url));calls++;return response({},429);},
   sleep:async()=>{controller.abort();throw new Error(key);}}});
 await assert.rejects(()=>p.embed({texts:["a"],purpose:"query",signal:controller.signal}),failure("cancelled",1));assert.equal(calls,1);
});
const environment=():NodeJS.ProcessEnv=>({EMBEDDING_PROVIDER:"llama_cpp",EMBEDDING_BASE_URL:"http://127.0.0.1:18081/v1",EMBEDDING_MODEL:"test/embedding-fixture",EMBEDDING_DIMENSIONS:"3",EMBEDDING_DISTANCE_METRIC:"cosine",
 EMBEDDING_CONFIG_VERSION:"fixture-v1",EMBEDDING_POOLING:"last",EMBEDDING_NORMALIZATION:"server_l2",EMBEDDING_CONTEXT_TOKENS:"2048",EMBEDDING_RUNTIME_VERSION:"fixture-build",EMBEDDING_ARTIFACT:"fixture.gguf"});
function preflight(url:string):Response {
 const p=profile();return url.endsWith("/models")?response({data:[{id:p.model,meta:{n_embd:p.dimensions,n_ctx:p.contextTokens}}]}):url.endsWith("/props")?
  response({build_info:p.runtimeVersion,model_path:"/fixture/fixture.gguf",default_generation_settings:{n_ctx:2048}}):response({tokens:[1,2,3,4,5,6]});
}
test("explicit configuration can be validated without a credential or database model ID",()=>{
 const config=readEmbeddingConfiguration(environment());assert.equal(config.apiKey,undefined);assert.deepEqual(config.profile,profile());
 assert.equal(embeddingConfigurationHash(config.profile).length,64);
});
for(const change of [{EMBEDDING_MODEL:""},{EMBEDDING_MODEL:"bad\nmodel"},{EMBEDDING_DIMENSIONS:"3.5"},{EMBEDDING_DIMENSIONS:"0"},
 {EMBEDDING_PROVIDER:"other"},{EMBEDDING_DISTANCE_METRIC:"l2"},{EMBEDDING_POOLING:"mean"},{EMBEDDING_CONFIG_VERSION:""},
 {EMBEDDING_QUERY_PREFIX_JSON:"not json"},{EMBEDDING_QUERY_PREFIX_JSON:"123"},{EMBEDDING_MAX_ATTEMPTS:"6"},{EMBEDDING_TOTAL_TIMEOUT_MS:"0"},
 {EMBEDDING_ATTEMPT_TIMEOUT_MS:"60001"},{LLAMA_EMBEDDING_API_KEY:"bad\nkey"}])test("invalid configuration is a fixed safe error: "+Object.keys(change)[0],()=>{
 assert.throws(()=>readEmbeddingConfiguration({...environment(),...change}),failure("invalid_configuration"));
});
test("credentials and operational retry changes do not change semantic profile hash",()=>{
 const a=readEmbeddingConfiguration(environment()),b=readEmbeddingConfiguration({...environment(),LLAMA_EMBEDDING_API_KEY:key,EMBEDDING_MAX_ATTEMPTS:"1"});
 assert.equal(embeddingConfigurationHash(a.profile),embeddingConfigurationHash(b.profile));
});
test("changing model, dimensions, task, prefix or version changes configuration identity",()=>{
 const base=embeddingConfigurationHash(profile());
 for(const update of [(p:EmbeddingProfile)=>{p.model="test/other";},(p:EmbeddingProfile)=>{p.dimensions=4;},(p:EmbeddingProfile)=>{p.version="fixture-v2";},
  (p:EmbeddingProfile)=>{p.query.prefix="query: ";},(p:EmbeddingProfile)=>{p.runtimeVersion="fixture-build2";},(p:EmbeddingProfile)=>{p.artifact="different.gguf";}]){
  const p=profile();update(p);assert.notEqual(embeddingConfigurationHash(p),base);
 }
});

test("CLI dry-run needs no credential, database or paid request",async()=>{
 const directory=await mkdtemp(join(tmpdir(),"embedding-check-"));
 const run=(...args:string[])=>execFileSync(process.execPath,["--import",import.meta.resolve("tsx"),resolve("src/scripts/check-embedding-provider.ts"),...args],{cwd:directory,encoding:"utf8"});
 try {
  const missing=JSON.parse(run());assert.equal(missing.status,"embedding_not_configured");assert.equal(missing.provider_calls,0);
  await writeFile(join(directory,".env.embedding.local"),Object.entries({...environment(),LLAMA_EMBEDDING_API_KEY:""}).map(([k,v])=>k+"="+v).join("\n"));
  const valid=JSON.parse(run("--dry-run"));assert.equal(valid.status,"embedding_configuration_valid");assert.equal(valid.api_key_present,false);assert.equal(valid.provider_calls,0);
  await writeFile(join(directory,".env.embedding.local"),Object.entries({...environment(),EMBEDDING_BASE_URL:"https://outside.invalid/v1"}).map(([k,v])=>k+"="+v).join("\n"));
  assert.throws(()=>run("--live")); // Invalid endpoint rejected BEFORE transport.
 }finally{await rm(directory,{recursive:true,force:true});}
});
test("Retry-After cannot exceed remaining overall deadline",async()=>{
 const h=harness([response({},429,{"retry-after":"2"})],profile(),{attemptTimeoutMs:1000,totalTimeoutMs:1000});
 await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"query"}),failure("retry_after_exceeds_budget",1));assert.equal(h.calls.length,1);assert.deepEqual(h.waits,[]);
});
test("jitter stays within bounded backoff",async()=>{
 const waits:number[]=[];let n=0;
 const p=createLlamaEmbeddingProvider({profile:profile(),baseUrl:"http://127.0.0.1:18081/v1",apiKey:key,runtime:{fetch:async(url)=>String(url).endsWith("/embeddings")?(++n===1?response({},503):response(success())):preflight(String(url)),random:()=>0,sleep:async ms=>{waits.push(ms);}}});
 await p.embed({texts:["a"],purpose:"query"});assert.ok(waits[0]!>=125 && waits[0]!<=250);
});

for(const url of ["https://openrouter.ai/api/v1","http://100.116.20.59:8081/v1","http://outside.invalid/v1","http://127.0.0.1:8081/admin","http://u:p@127.0.0.1:8081/v1","http://127.0.0.1:8081/v1?target=evil"])
 test("rejects non-loopback or altered provider endpoint: "+url,()=>assert.throws(()=>readEmbeddingConfiguration({...environment(),EMBEDDING_BASE_URL:url}),failure("invalid_configuration")));
for(const mismatch of ["id","dimensions","context","build","artifact"])
 test("server configuration mismatch stops before embedding: "+mismatch,async()=>{
  let embeddings=0;
  const provider=createLlamaEmbeddingProvider({profile:profile(),baseUrl:"http://127.0.0.1:18081/v1",runtime:{fetch:async url=>{
   if(String(url).endsWith("/embeddings")){embeddings++;return response(success());}
   const raw=await preflight(String(url)).json() as any;
   if(String(url).endsWith("/models")){
    if(mismatch==="id")raw.data[0].id="other";if(mismatch==="dimensions")raw.data[0].meta.n_embd=1024;if(mismatch==="context")raw.data[0].meta.n_ctx=512;
   }
   if(String(url).endsWith("/props")){if(mismatch==="build")raw.build_info="changed";if(mismatch==="artifact")raw.model_path="/other.gguf";}
   return response(raw);
  }}});
  await assert.rejects(()=>provider.embed({texts:["a"],purpose:"document"}),failure("model_mismatch",1));assert.equal(embeddings,0);
 });
test("actual tokenizer rejects over-context input before any embedding call",async()=>{
 let embeddings=0;const provider=createLlamaEmbeddingProvider({profile:profile(),baseUrl:"http://127.0.0.1:18081/v1",runtime:{fetch:async url=>{
  if(String(url).endsWith("/tokenize"))return response({tokens:Array(2049).fill(1)});
  if(String(url).endsWith("/embeddings")){embeddings++;return response(success());}return preflight(String(url));
 }}});
 await assert.rejects(()=>provider.embed({texts:["a"],purpose:"document"}),failure("input_too_long",1));assert.equal(embeddings,0);
});
test("optional local auth is not fabricated and tokenization sees exact prefixed text",async()=>{
 const p=profile();p.query.prefix="Instruct: products\nQuery:";let tokenText="";
 const provider=createLlamaEmbeddingProvider({profile:p,baseUrl:"http://127.0.0.1:18081/v1",runtime:{fetch:async(url,init)=>{
  assert.equal((init!.headers as Record<string,string>).authorization,undefined);
  if(String(url).endsWith("/tokenize")){const body=JSON.parse(String(init!.body));tokenText=body.content;assert.equal(body.add_special,true);assert.equal(body.parse_special,true);}
  return String(url).endsWith("/embeddings")?response(success()):preflight(String(url));
 }}});
 const r=await provider.embed({texts:["ทีวี"],purpose:"query"});assert.equal(tokenText,"Instruct: products\nQuery:ทีวี");assert.deepEqual(r.tokenCounts,[6]);
});
test("usage mismatch cannot hide token truncation",async()=>{
 const raw=success();raw.usage.prompt_tokens=5;raw.usage.total_tokens=5;const h=harness([response(raw)]);
 await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"document"}),failure("invalid_response",1));
});
test("v1 vectors must already have server L2 normalization",async()=>{
 const h=harness([response(success([[1,2,3]]))]);await assert.rejects(()=>h.provider.embed({texts:["a"],purpose:"document"}),failure("invalid_response",1));
});
test("launch command is inspectable without starting/restarting a GPU process",async()=>{
 const directory=await mkdtemp(join(tmpdir(),"llama-launch-"));
 try{
  const sentinel=join(directory,"do-not-create");
  const out=execFileSync("bash",[resolve("scripts/start-embedding-server.sh")],{encoding:"utf8",env:{...process.env,LLAMA_START_DRY_RUN:"1",LLAMA_SERVER_BIN:"/fake/llama-server",LLAMA_MODEL_FILE:"/models/$(touch "+sentinel+") name.gguf"}});
  assert.match(out,/--pooling last/);assert.match(out,/--host 127\.0\.0\.1/);assert.match(out,/--port 8081/);assert.match(out,/--device Vulkan0/);assert.match(out,/-ngl 99/);assert.match(out,/-c 2048/);assert.equal(existsSync(sentinel),false);
 }finally{await rm(directory,{recursive:true,force:true});}
});

test("occupied server port preserves existing process without launching another model",async()=>{
 const dir=await mkdtemp(join(tmpdir(),"llama-existing-"));
 try{
  const marker=join(dir,"launched"),model=join(dir,"model.gguf"),binary=join(dir,"llama-server");
  await writeFile(join(dir,"uname"),"#!/bin/sh\necho Linux\n",{mode:0o755});
  await writeFile(join(dir,"ss"),"#!/bin/sh\necho 'LISTEN 0 4096 127.0.0.1:8081'\n",{mode:0o755});
  await writeFile(binary,"#!/bin/sh\ntouch '"+marker+"'\n",{mode:0o755});await writeFile(model,"");
  const out=execFileSync("bash",[resolve("scripts/start-embedding-server.sh")],{encoding:"utf8",env:{...process.env,PATH:dir+":"+process.env.PATH,LLAMA_START_DRY_RUN:"0",LLAMA_SERVER_BIN:binary,LLAMA_MODEL_FILE:model}});
  assert.match(out,/already bound/);assert.equal(existsSync(marker),false);
 }finally{await rm(dir,{recursive:true,force:true});}
});

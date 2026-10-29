import {createHash} from "node:crypto";
import {EmbeddingError,type EmbeddingProfile,type EmbeddingResult} from "./embedding.types.js";
import {embeddingConfigurationHash,validateLocalEmbeddingUrl} from "../config/embedding.config.js";
import {DEFAULT_QUERY_CACHE_CONFIG,validateQueryCacheConfig,type QueryCacheConfig} from "../config/query-cache.config.js";
import type {QueryEmbeddingProvider} from "../services/category-semantic.service.js";
export interface QueryCacheIdentity {modelId:string;profile:EmbeddingProfile;configurationHash:string;artifactSha256:string;baseUrl:string;apiKey?:string;maxInputBytes?:number}
interface Stored {result:EmbeddingResult;expires:number;bytes:number}
interface Flight {controller:AbortController;promise:Promise<EmbeddingResult>;waiters:number;done:boolean}
const safeError=(e:unknown)=>e instanceof EmbeddingError?new EmbeddingError(e.code,e.attempts,e.httpStatus):new EmbeddingError("network_error");
const hash=(value:unknown)=>createHash("sha256").update(JSON.stringify(value)).digest("hex");
export function createQueryEmbeddingCache(config:QueryCacheConfig=DEFAULT_QUERY_CACHE_CONFIG,runtime:{now?:()=>number}={}) {
 const bounds={...config};validateQueryCacheConfig(bounds);const now=runtime.now??(()=>performance.now());
 const entries=new Map<string,Stored>(),pending=new Map<string|symbol,Flight>(),active=new Set<Flight>();
 let bytes=0,closed=false,closing:Promise<void>|undefined;
 const counts={hits:0,misses:0,shared:0,evictions:0,rejected:0};
 const remove=(key:string,eviction=false)=>{const item=entries.get(key);if(item){bytes-=item.bytes;entries.delete(key);if(eviction)counts.evictions++;}};
 const prune=()=>{const time=now();for(const [key,item] of entries)if(item.expires<=time)remove(key,true);};
 const validate=(r:EmbeddingResult,i:QueryCacheIdentity):EmbeddingResult=>{
  if(!r || typeof r!=="object" || r.provider!=="llama_cpp" || r.model!==i.profile.model || r.dimensions!==i.profile.dimensions || r.configurationHash!==i.configurationHash || r.purpose!=="query" ||
   !Array.isArray(r.vectors) || r.vectors.length!==1 || !Array.isArray(r.vectors[0]) || r.vectors[0].length!==i.profile.dimensions ||
   !r.vectors[0].every(v=>typeof v==="number" && Number.isFinite(v) && Number.isFinite(Math.fround(v))) ||
   Math.abs(Math.sqrt(r.vectors[0].reduce((sum,v)=>sum+v*v,0))-1)>0.01 ||
   !Array.isArray(r.tokenCounts) || r.tokenCounts.length!==1 || !Number.isSafeInteger(r.tokenCounts[0]) || r.tokenCounts[0]!<1 || r.tokenCounts[0]!>i.profile.contextTokens ||
   !r.usage || !Number.isSafeInteger(r.usage.promptTokens) || !Number.isSafeInteger(r.usage.totalTokens) || r.usage.promptTokens!==r.tokenCounts[0] || r.usage.promptTokens<0 || r.usage.totalTokens<r.usage.promptTokens ||
   !Number.isSafeInteger(r.attempts) || r.attempts<1 || r.attempts>5)throw new EmbeddingError("invalid_response");
  // เก็บเฉพาะผลที่ตรวจแล้ว ไม่เก็บข้อความหรือข้อมูลเพิ่มเติมจาก provider
  return {provider:"llama_cpp",model:r.model,dimensions:r.dimensions,configurationHash:r.configurationHash,purpose:"query",
   vectors:[r.vectors[0].slice()],tokenCounts:r.tokenCounts.slice(),usage:{promptTokens:r.usage.promptTokens,totalTokens:r.usage.totalTokens},attempts:r.attempts};
 };
 const answer=(r:EmbeddingResult,status:NonNullable<EmbeddingResult["cache_status"]>):EmbeddingResult=>{
  const copy=structuredClone(r);copy.cache_status=status;
  if(status==="hit" || status==="shared"){copy.attempts=0;copy.usage={promptTokens:0,totalTokens:0};}
  return copy;
 };
 const store=(key:string,result:EmbeddingResult)=>{
  const size=Math.max(Buffer.byteLength(JSON.stringify(result)),result.dimensions*16+Buffer.byteLength(result.model)*4+2048);
  if(size>bounds.maxBytes || closed)return;
  prune();remove(key);while(entries.size>=bounds.maxEntries || bytes+size>bounds.maxBytes){const first=entries.keys().next().value;if(first===undefined)break;remove(first,true);}
  entries.set(key,{result:structuredClone(result),expires:now()+bounds.ttlMs,bytes:size});bytes+=size;
 };
 const wait=(flight:Flight,signal:AbortSignal|undefined,status:NonNullable<EmbeddingResult["cache_status"]>):Promise<EmbeddingResult>=>new Promise((resolve,reject)=>{
  if(signal?.aborted){if(!flight.waiters && !flight.done)flight.controller.abort();reject(new EmbeddingError("cancelled"));return;}
  flight.waiters++;let settled=false;
  const finish=(error?:unknown,r?:EmbeddingResult)=>{
   if(settled)return;settled=true;signal?.removeEventListener("abort",abort);flight.waiters--;
   if(!flight.waiters && !flight.done)flight.controller.abort();
   if(error!==undefined)reject(safeError(error));else resolve(answer(r!,status));
  };
  const abort=()=>finish(new EmbeddingError("cancelled"));signal?.addEventListener("abort",abort,{once:true});
  flight.promise.then(r=>finish(undefined,r),e=>finish(e));
 });
 return {
  wrap(source:QueryEmbeddingProvider,raw:QueryCacheIdentity):QueryEmbeddingProvider {
   const identity=structuredClone(raw);const baseUrl=validateLocalEmbeddingUrl(identity.baseUrl);
   if(!/^EM\d{4}$/.test(identity.modelId) || !/^[0-9a-f]{64}$/.test(identity.artifactSha256) || embeddingConfigurationHash(identity.profile)!==identity.configurationHash)throw new Error("Invalid cache identity");
   const maxInputBytes=identity.maxInputBytes??16000;if(!Number.isInteger(maxInputBytes) || maxInputBytes<1 || maxInputBytes>128000)throw new Error("Invalid cache input bound");
   const namespace=[identity.modelId,identity.configurationHash,identity.artifactSha256,baseUrl,hash(identity.apiKey??"")];
   return {async embed(input) {
    if(closed || input.signal?.aborted)throw new EmbeddingError("cancelled");
    if(input.purpose!=="query" || !Array.isArray(input.texts) || input.texts.length!==1 || typeof input.texts[0]!=="string" || !input.texts[0].trim())throw new EmbeddingError("invalid_input");
    const text=input.texts[0];if(Buffer.byteLength(identity.profile.query.prefix+text)>maxInputBytes)throw new EmbeddingError("invalid_input");
    const key=hash([...namespace,"query",text]);prune();
    if(bounds.enabled){const saved=entries.get(key);if(saved){entries.delete(key);entries.set(key,saved);counts.hits++;return answer(saved.result,"hit");}}
    const running=bounds.enabled?pending.get(key):undefined;
    if(running && !running.controller.signal.aborted){
     if(running.waiters>=bounds.maxWaiters){counts.rejected++;throw new EmbeddingError("capacity_exceeded");}
     counts.shared++;return wait(running,input.signal,"shared");
    }
    if(active.size>=bounds.maxInFlight){counts.rejected++;throw new EmbeddingError("capacity_exceeded");}
    const taskKey=bounds.enabled?key:Symbol();counts.misses++;
    const task:Flight={controller:new AbortController(),promise:undefined!,waiters:0,done:false};pending.set(taskKey,task);active.add(task);
    // งานที่ไม่ตอบสนองต่อ abort ยังถือครอง slot จน provider จบ เพื่อไม่สะสมงานเกินขอบเขต
    const upstream=Promise.resolve().then(()=>{
     if(task.controller.signal.aborted)throw new EmbeddingError("cancelled");
     return source.embed({texts:[text],purpose:"query",signal:task.controller.signal});
    });
    void upstream.then(()=>active.delete(task),()=>active.delete(task));
    task.promise=(async()=>{
     let timer:ReturnType<typeof setTimeout>|undefined,abort:()=>void=()=>{};
     try{
      const deadline=new Promise<never>((_,reject)=>{
       abort=()=>reject(new EmbeddingError("cancelled"));task.controller.signal.addEventListener("abort",abort,{once:true});
       timer=setTimeout(()=>{reject(new EmbeddingError("timeout"));task.controller.abort();},bounds.timeoutMs);
      });
      const result=validate(await Promise.race([upstream,deadline]),identity);
      if(closed || task.controller.signal.aborted)throw new EmbeddingError("cancelled");
      if(bounds.enabled)store(key,result);return result;
     }catch(e){throw safeError(e);}
     finally{clearTimeout(timer);task.controller.signal.removeEventListener("abort",abort);task.done=true;if(pending.get(taskKey)===task)pending.delete(taskKey);}
    })();
    void task.promise.catch(()=>{});
    return wait(task,input.signal,bounds.enabled?"miss":"disabled");
   }};
  },
  stats(){prune();return {...counts,entries:entries.size,bytes,in_flight:active.size,pending_queries:pending.size};},
  close(){return closing??=(async()=>{closed=true;entries.clear();bytes=0;const tasks=[...pending.values()];for(const task of active)task.controller.abort();await Promise.allSettled(tasks.map(t=>t.promise));pending.clear();active.clear();})();},
 };
}
export type QueryEmbeddingCache=ReturnType<typeof createQueryEmbeddingCache>;

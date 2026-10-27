import {validateEmbeddingBounds,validateEmbeddingProfile,embeddingConfigurationHash,DEFAULT_EMBEDDING_BOUNDS,validateLocalEmbeddingUrl} from "../config/embedding.config.js";
import {EmbeddingError,type EmbeddingBounds,type EmbeddingProfile,type EmbeddingPurpose,type EmbeddingResult} from "./embedding.types.js";

export interface EmbeddingRuntime {
 fetch:typeof fetch;
 now:()=>number;
 random:()=>number;
 sleep:(milliseconds:number,signal?:AbortSignal)=>Promise<void>;
}
const sleep:EmbeddingRuntime["sleep"]=(milliseconds,signal)=>new Promise((resolve,reject)=>{
 if(signal?.aborted){reject(new EmbeddingError("cancelled"));return;}
 const abort=()=>{clearTimeout(timer);signal?.removeEventListener("abort",abort);reject(new EmbeddingError("cancelled"));};
 const timer=setTimeout(()=>{signal?.removeEventListener("abort",abort);resolve();},milliseconds);
 signal?.addEventListener("abort",abort,{once:true});
});
const record=(x:unknown):x is Record<string,unknown>=>typeof x==="object" && x!==null && !Array.isArray(x);
const retryable=(status:number)=>status===408 || status===429 || (status>=500 && status<=599);
async function readJson(response:Response,limit:number,signal:AbortSignal):Promise<unknown> {
 if(!/^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/i.test(response.headers.get("content-type")??""))throw new EmbeddingError("invalid_response");
 const length=response.headers.get("content-length");
 if(length && /^\d+$/.test(length) && Number(length)>limit){await response.body?.cancel().catch(()=>{});throw new EmbeddingError("response_too_large");}
 if(!response.body)throw new EmbeddingError("invalid_response");
 const reader=response.body.getReader();let size=0;const chunks:Uint8Array[]=[];
 const abort=()=>{void reader.cancel().catch(()=>{});};signal.addEventListener("abort",abort,{once:true});
 if(signal.aborted)abort();
 try {for(;;){const {value,done}=await reader.read();if(done)break;if(value){size+=value.byteLength;
   if(size>limit){void reader.cancel().catch(()=>{});throw new EmbeddingError("response_too_large");}chunks.push(value);}}}
 finally {signal.removeEventListener("abort",abort);reader.releaseLock();}
 try{return JSON.parse(Buffer.concat(chunks,size).toString("utf8")) as unknown;}catch{throw new EmbeddingError("invalid_response");}
}
function decode(value:unknown,count:number,profile:EmbeddingProfile):Pick<EmbeddingResult,"vectors"|"usage"> {
 function invalid():never {throw new EmbeddingError("invalid_response");}
 if(!record(value) || value.error!==undefined || value.object!=="list" || value.model!==profile.model || !Array.isArray(value.data) || value.data.length!==count)invalid();
 const vectors:number[][]=Array(count);const seen=new Set<number>();
 for(const row of value.data){
  if(!record(row) || row.object!=="embedding" || typeof row.index!=="number" || !Number.isInteger(row.index) || row.index<0 || row.index>=count || seen.has(row.index) ||
    !Array.isArray(row.embedding) || row.embedding.length!==profile.dimensions)invalid();
  const vector=row.embedding as unknown[];
  if(!vector.every(v=>typeof v==="number" && Number.isFinite(v) && Number.isFinite(Math.fround(v))) ||
    !vector.some(v=>Math.fround(v as number)!==0))invalid();
  const norm=Math.sqrt((vector as number[]).reduce((sum,v)=>sum+v*v,0));if(Math.abs(norm-1)>0.01)invalid();
  seen.add(row.index);vectors[row.index]=[...vector] as number[];
 }
 const usage=value.usage;
 if(!record(usage) || typeof usage.prompt_tokens!=="number" || !Number.isSafeInteger(usage.prompt_tokens) || usage.prompt_tokens<0 ||
    typeof usage.total_tokens!=="number" || !Number.isSafeInteger(usage.total_tokens) || usage.total_tokens<usage.prompt_tokens)invalid();
 return {vectors,usage:{promptTokens:usage.prompt_tokens,totalTokens:usage.total_tokens}};
}
export function createLlamaEmbeddingProvider(options:{profile:EmbeddingProfile;baseUrl:string;apiKey?:string;bounds?:EmbeddingBounds;runtime?:Partial<EmbeddingRuntime>}) {
 const profile=structuredClone(options.profile),bounds={...(options.bounds??DEFAULT_EMBEDDING_BOUNDS)};
 validateEmbeddingProfile(profile);validateEmbeddingBounds(bounds);
 const baseUrl=validateLocalEmbeddingUrl(options.baseUrl);
 if(options.apiKey!==undefined && (typeof options.apiKey!=="string" || !/^[\x21-\x7e]{1,512}$/.test(options.apiKey)))throw new EmbeddingError("invalid_configuration");
 const apiKey=options.apiKey,configurationHash=embeddingConfigurationHash(profile);
 const runtime:EmbeddingRuntime={fetch:globalThis.fetch,now:()=>performance.now(),random:Math.random,sleep,...options.runtime};
 return {async embed(input:{texts:readonly string[];purpose:EmbeddingPurpose;signal?:AbortSignal}):Promise<EmbeddingResult> {
  if(!input || !["query","document"].includes(input.purpose) || !Array.isArray(input.texts) || input.texts.length<1 || input.texts.length>bounds.maxBatchSize ||
    Array.from(input.texts).some(t=>typeof t!=="string" || !t.trim()) || (input.signal!==undefined && !(input.signal instanceof AbortSignal)))throw new EmbeddingError("invalid_input");
  const signal=input.signal;const purpose=input.purpose;const settings=profile[purpose];const texts=input.texts.map(t=>settings.prefix+t);
  const sizes=texts.map(t=>Buffer.byteLength(t));if(sizes.some(s=>s>bounds.maxInputBytes) || sizes.reduce((a,b)=>a+b,0)>bounds.maxBatchBytes)throw new EmbeddingError("invalid_input");
  const body=JSON.stringify({model:profile.model,input:texts,encoding_format:"float"});
  const deadline=runtime.now()+bounds.totalTimeoutMs;let attempts=0;
  while(attempts<bounds.maxAttempts){
   if(signal?.aborted)throw new EmbeddingError("cancelled",attempts);
   const remaining=deadline-runtime.now();if(remaining<=0)throw new EmbeddingError("deadline_exceeded",attempts);
   attempts++;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined,abort:(()=>void)|undefined;
   let retryAfter:string|null=null;
   try {
    const timeout=new Promise<never>((_,reject)=>{
     timer=setTimeout(()=>{reject(new EmbeddingError("timeout"));controller.abort();},Math.min(bounds.attemptTimeoutMs,remaining));
     abort=()=>{reject(new EmbeddingError("cancelled"));controller.abort();};signal?.addEventListener("abort",abort,{once:true});
    });
    const operation=(async()=>{
     const request=async(url:string,payload?:unknown)=>{
      if(controller.signal.aborted)throw new EmbeddingError("cancelled");
      const response=await runtime.fetch(url,{method:payload===undefined?"GET":"POST",redirect:"error",signal:controller.signal,
       headers:{...(apiKey?{authorization:"Bearer "+apiKey}:{}),"content-type":"application/json"},
       ...(payload===undefined?{}:{body:typeof payload==="string"?payload:JSON.stringify(payload)})});
      if(!response.ok){retryAfter=response.headers.get("retry-after");void response.body?.cancel().catch(()=>{});throw new EmbeddingError("http_error",0,response.status);}
      return readJson(response,bounds.maxResponseBytes,controller.signal);
     };
     const models=await request(baseUrl+"/models");
     if(!record(models) || !Array.isArray(models.data))throw new EmbeddingError("model_mismatch");
     const model=models.data.find(m=>record(m) && m.id===profile.model);
     if(!record(model) || !record(model.meta) || model.meta.n_embd!==profile.dimensions || model.meta.n_ctx!==profile.contextTokens)throw new EmbeddingError("model_mismatch");
     const props=await request(baseUrl.replace(/\/v1$/,"")+"/props");
     if(!record(props) || props.build_info!==profile.runtimeVersion || typeof props.model_path!=="string" || props.model_path.split(/[\\/]/).at(-1)!==profile.artifact ||
       !record(props.default_generation_settings) || props.default_generation_settings.n_ctx!==profile.contextTokens)throw new EmbeddingError("model_mismatch");
     const tokenCounts:number[]=[];
     for(const text of texts){
      const result=await request(baseUrl.replace(/\/v1$/,"")+"/tokenize",{content:text,add_special:true,parse_special:true,with_pieces:false});
      if(!record(result) || !Array.isArray(result.tokens) || result.tokens.length<1 || !result.tokens.every(t=>typeof t==="number" && Number.isSafeInteger(t) && t>=0))throw new EmbeddingError("invalid_response");
      if(result.tokens.length>profile.contextTokens)throw new EmbeddingError("input_too_long");tokenCounts.push(result.tokens.length);
     }
     const decoded=decode(await request(baseUrl+"/embeddings",body),texts.length,profile);
     if(decoded.usage.promptTokens!==tokenCounts.reduce((sum,n)=>sum+n,0))throw new EmbeddingError("invalid_response");
     return {...decoded,tokenCounts};
    })();
    const result=await Promise.race([operation,timeout]);
    if(runtime.now()>=deadline)throw new EmbeddingError("deadline_exceeded");
    return {provider:"llama_cpp",model:profile.model,dimensions:profile.dimensions,configurationHash,purpose,...result,attempts};
   }catch(error){
    const e=error instanceof EmbeddingError?error:new EmbeddingError("network_error");
    if(signal?.aborted || e.code==="cancelled")throw new EmbeddingError("cancelled",attempts);
    const canRetry=e.code==="network_error" || e.code==="timeout" || (e.code==="http_error" && e.httpStatus!==null && retryable(e.httpStatus));
    if(!canRetry || attempts>=bounds.maxAttempts)throw new EmbeddingError(e.code,attempts,e.httpStatus);
    if(runtime.now()>=deadline)throw new EmbeddingError("deadline_exceeded",attempts,e.httpStatus);
    let wait=Math.min(bounds.maxDelayMs,bounds.baseDelayMs*2**(attempts-1))*(0.5+Math.max(0,Math.min(1,runtime.random()))*0.5);
    if(retryAfter!==null){
     const numeric=/^\d+(?:\.\d+)?$/.test(retryAfter);
     const seconds=numeric?Number(retryAfter)*1000:NaN;
     const date=Number.isFinite(seconds)?NaN:Date.parse(retryAfter);
     const requested=numeric?seconds:Number.isFinite(date)?Math.max(0,date-Date.now()):0;
     wait=Math.max(wait,requested);
    }
    if(wait>bounds.maxDelayMs || wait>=deadline-runtime.now())throw new EmbeddingError("retry_after_exceeds_budget",attempts,e.httpStatus);
    // Release per-attempt resources BEFORE waiting. Cancellation remains active in sleep itself.
    clearTimeout(timer);if(abort)signal?.removeEventListener("abort",abort);controller.abort();
    try {await runtime.sleep(Math.ceil(wait),signal);}catch{throw new EmbeddingError(signal?.aborted?"cancelled":"network_error",attempts,e.httpStatus);}
   }finally{clearTimeout(timer);if(abort)signal?.removeEventListener("abort",abort);controller.abort();}
  }
  throw new EmbeddingError("deadline_exceeded",attempts);
 }};
}

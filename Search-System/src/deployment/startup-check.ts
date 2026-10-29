import {boundedJson} from "./private-http.js";
import {readProductHybridConfig} from "../config/product-hybrid.config.js";
import {createLlamaEmbeddingProvider} from "../embeddings/llama-embedding.provider.js";
import type {QueryEmbeddingProvider} from "../services/category-semantic.service.js";
import type {Pool} from "pg";
export async function waitForModel(baseUrl:string,options:{deadlineMs?:number;intervalMs?:number;fetcher?:typeof fetch;apiKey?:string}={}) {
 const deadlineMs=options.deadlineMs??60000,intervalMs=options.intervalMs??1000;
 if(!Number.isInteger(deadlineMs)||deadlineMs<1||deadlineMs>60000||!Number.isInteger(intervalMs)||intervalMs<1||intervalMs>1000)throw new Error("Invalid readiness bound");
 const u=new URL(baseUrl);if(u.origin!=="http://127.0.0.1:8081"||!["/v1","/v1/"].includes(u.pathname)||u.username||u.password||u.search||u.hash)throw new Error("Require server-local model");
 const begin=performance.now(),fetcher=options.fetcher??fetch;let attempts=0;
 while(performance.now()-begin<deadlineMs){attempts++;const remaining=deadlineMs-(performance.now()-begin),controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  try{
   const request=(async()=>{const r=await fetcher(new URL("/health",u),{redirect:"error",signal:controller.signal,...(options.apiKey?{headers:{authorization:"Bearer "+options.apiKey}}:{})});if(!r.ok)return false;const b=await boundedJson(r,8192);return Boolean(b&&typeof b==="object"&&"status" in b&&b.status==="ok");})();
   const timeout=new Promise<false>(resolve=>{timer=setTimeout(()=>{controller.abort();resolve(false);},Math.min(3000,remaining));});
   if(await Promise.race([request,timeout]))return {attempts};
   // ไม่เพิ่มงานซ้อนเมื่อ transport ไม่ยอมยกเลิก
   if(controller.signal.aborted)throw new Error("Model health timeout");
  }catch{if(controller.signal.aborted)throw new Error("Model health timeout");}finally{clearTimeout(timer);}
  const left=deadlineMs-(performance.now()-begin);if(left>0)await new Promise(r=>setTimeout(r,Math.min(intervalMs,left)));
 }
 throw new Error("Model not ready");
}
export async function verifyStartup(pool:Pick<Pool,"connect">,env:NodeJS.ProcessEnv,options:{provider?:QueryEmbeddingProvider;wait?:typeof waitForModel}={}) {
 const c=readProductHybridConfig(env);if(!c)throw new Error("Hybrid configuration required");
 const ready=await (options.wait??waitForModel)(c.baseUrl,{apiKey:c.apiKey});
 const client=await pool.connect();let state;
 try {await client.query("BEGIN READ ONLY");
 state=(await client.query("SELECT catalog_search.product_semantic_state_v1($1::text[],$2,$3,$4) AS state",[["AP0101"],c.modelId,c.configurationHash,c.artifactSha256])).rows[0]?.state;
  if(!state||!["ready","coverage_incomplete","empty_scope"].includes(state.status))throw new Error("Registered model/profile unavailable");
 }finally{try{await client.query("ROLLBACK");}finally{client.release();}}
 const p=options.provider??createLlamaEmbeddingProvider({...c,bounds:{...c.bounds,maxAttempts:1,attemptTimeoutMs:4000,totalTimeoutMs:5000}});
 const vector=await p.embed({texts:["ตรวจความพร้อมระบบค้นหาสินค้า"],purpose:"query",signal:AbortSignal.timeout(5000)});
 if(vector.configurationHash!==c.configurationHash||vector.model!==c.profile.model||vector.dimensions!==c.profile.dimensions||vector.purpose!=="query"||vector.vectors.length!==1||vector.vectors[0]!.length!==c.profile.dimensions||!vector.vectors[0]!.every(Number.isFinite)||Math.abs(Math.hypot(...vector.vectors[0]!)-1)>.01)throw new Error("Model output mismatch");
 return {status:"server_startup_ready",model_health_attempts:ready.attempts,model_id:c.modelId,configuration_hash:c.configurationHash,dimensions:c.profile.dimensions,scope_state:state.status,embedding_calls:1,database_writes:0};
}

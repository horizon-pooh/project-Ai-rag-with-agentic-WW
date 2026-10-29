import {createHash} from "node:crypto";
import {Ajv} from "ajv";
import {parseToolSearchRequest,validateToolSearchResponse} from "../validation/tool-search.validation.js";
import {validDetailsResponse} from "../schemas/tool-http.schema.js";
import type {ToolSearchRequest,ToolSearchResponse} from "../types/tool-search.types.js";
export type BenchmarkProbe={id:string;kind:"search";request:ToolSearchRequest;expected_status:ToolSearchResponse["status"]}|{id:string;kind:"details";article_id:number;expected_status:"ok"|"not_found"};
export interface BenchmarkWorkload {version:1;id:string;corpus_id:string;probes:BenchmarkProbe[]}
const label={type:"string",pattern:"^[A-Za-z0-9_.-]{1,80}$"};
const schema={type:"object",additionalProperties:false,required:["version","id","corpus_id","probes"],properties:{version:{const:1},id:label,corpus_id:{type:"string",minLength:1,maxLength:200},probes:{type:"array",minItems:1,maxItems:30,items:{oneOf:[
 {type:"object",additionalProperties:false,required:["id","kind","request","expected_status"],properties:{id:label,kind:{const:"search"},request:{type:"object"},expected_status:{enum:["ok","no_matches","needs_clarification","constraint_conflict"]}}},
 {type:"object",additionalProperties:false,required:["id","kind","article_id","expected_status"],properties:{id:label,kind:{const:"details"},article_id:{type:"integer",minimum:100000,maximum:199999},expected_status:{enum:["ok","not_found"]}}}
]}}}};
const valid=new Ajv({strict:true,strictNumbers:true}).compile(schema);
export function parseBenchmarkWorkload(raw:unknown):BenchmarkWorkload {
 if(!valid(raw))throw new Error("Invalid benchmark workload");const workload=structuredClone(raw) as unknown as BenchmarkWorkload;
 if(new Set(workload.probes.map(p=>p.id)).size!==workload.probes.length)throw new Error("Duplicate probe ID");
 if(workload.probes.some(p=>p.kind==="search"&&!parseToolSearchRequest(p.request).valid))throw new Error("Invalid benchmark request");return workload;
}
export function latencySummary(values:number[]) {
 if(values.some(v=>!Number.isFinite(v)||v<0))throw new Error("Invalid timing");
 const sorted=values.slice().sort((a,b)=>a-b),at=(p:number)=>sorted.length?sorted[Math.max(0,Math.ceil(p*sorted.length)-1)]!:null;
 return {samples:sorted.length,min_ms:sorted[0]??null,p50_ms:at(.5),p95_ms:at(.95),max_ms:sorted.at(-1)??null};
}
export interface BenchmarkOptions {iterations:number;concurrency:number;warmup:number;deadlineMs:number;requestTimeoutMs:number}
export const DEFAULT_BENCHMARK_OPTIONS:BenchmarkOptions={iterations:40,concurrency:1,warmup:4,deadlineMs:120000,requestTimeoutMs:20000};
export function validateBenchmarkOptions(o:BenchmarkOptions) {
 for(const [value,min,max] of [[o.iterations,1,300],[o.concurrency,1,10],[o.warmup,0,20],[o.deadlineMs,1,300000],[o.requestTimeoutMs,1,20000]])if(!Number.isInteger(value)||value<min||value>max)throw new Error("Invalid benchmark bounds");
}
export interface BenchmarkReply {http_status:number;body:unknown}
export interface BenchmarkSample {sequence:number;probe_id:string;elapsed_ms:number;ok:boolean;failure:string|null;status:string;http_status:number|null;semantic_available:boolean;warning_codes:string[]}
function inspect(probe:BenchmarkProbe,reply:BenchmarkReply) {
 const fail=(reason:string,status="invalid_response")=>({ok:false,failure:reason,status,http_status:reply.http_status,semantic_available:false,warning_codes:[] as string[]});
 if(probe.kind==="details"){
  if(reply.http_status===404 && probe.expected_status==="not_found" && reply.body!==null && typeof reply.body==="object" && "status" in reply.body && reply.body.status==="not_found")return {ok:true,failure:null,status:"not_found",http_status:404,semantic_available:false,warning_codes:[]};
  if(reply.http_status!==200||!validDetailsResponse(reply.body))return fail("details_contract");
  const b=reply.body as {status:string;details:{product:{article_id:number}}};
  if(b.status!==probe.expected_status||b.details.product.article_id!==probe.article_id)return fail("unexpected_details");
  return {ok:true,failure:null,status:"ok",http_status:200,semantic_available:false,warning_codes:[]};
 }
 if(!validateToolSearchResponse(reply.body).valid)return fail("response_contract");
 const b=reply.body as ToolSearchResponse;
 if(reply.http_status>=500||b.status==="search_unavailable")return fail("search_unavailable",b.status);
 if(reply.http_status!==200 && !(reply.http_status===409&&b.status==="constraint_conflict"))return fail("http_status",b.status);
 const success=b.status==="ok"||b.status==="no_matches";
 return {ok:b.status===probe.expected_status,failure:b.status===probe.expected_status?null:"unexpected_status",status:b.status,http_status:reply.http_status,semantic_available:success?b.semantic_available:false,warning_codes:success?b.warnings.map(w=>w.code):[]};
}
export async function runSearchBenchmark(workload:BenchmarkWorkload,call:(probe:BenchmarkProbe,signal:AbortSignal)=>Promise<BenchmarkReply>,options:BenchmarkOptions=DEFAULT_BENCHMARK_OPTIONS) {
 options={...options};workload=structuredClone(workload);validateBenchmarkOptions(options);const started=performance.now(),samples:BenchmarkSample[]=[],warmups:BenchmarkSample[]=[];let next=0,stopped="complete";
 const execute=async(probe:BenchmarkProbe,sequence:number):Promise<{sample:BenchmarkSample;timedOut:boolean}>=>{
  const controller=new AbortController(),remaining=options.deadlineMs-(performance.now()-started),ms=Math.max(1,Math.min(options.requestTimeoutMs,remaining));
  let timer:ReturnType<typeof setTimeout>|undefined,timedOut=false;const begin=performance.now();
  try{
   const timeout=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{timedOut=true;controller.abort();reject(new Error("timeout"));},ms);});
   const reply=await Promise.race([Promise.resolve().then(()=>call(structuredClone(probe),controller.signal)),timeout]);
   return {sample:{sequence,probe_id:probe.id,elapsed_ms:performance.now()-begin,...inspect(probe,reply)},timedOut:false};
  }catch{return {sample:{sequence,probe_id:probe.id,elapsed_ms:performance.now()-begin,ok:false,failure:timedOut?"timeout":"transport_error",status:"execution_error",http_status:null,semantic_available:false,warning_codes:[]},timedOut};}
  finally{clearTimeout(timer);}
 };
 for(let i=0;i<options.warmup;i++){
  if(performance.now()-started>=options.deadlineMs){stopped="budget";break;}
  const r=await execute(workload.probes[i%workload.probes.length]!,i);warmups.push(r.sample);if(r.timedOut){stopped="timeout";break;}
 }
 const measuredStart=performance.now();
 const worker=async()=>{while(stopped==="complete"){
  if(next>=options.iterations)return;if(performance.now()-started>=options.deadlineMs){stopped="budget";return;}
  const index=next++,r=await execute(workload.probes[index%workload.probes.length]!,index);samples.push(r.sample);
  // หาก transport ไม่ยอม abort จะไม่เปิดงานใหม่ใน worker นี้
  if(r.timedOut){stopped="timeout";return;}
 }};
 if(stopped==="complete")await Promise.all(Array.from({length:Math.min(options.concurrency,options.iterations)},worker));
 const wall=performance.now()-measuredStart;samples.sort((a,b)=>a.sequence-b.sequence);
 const summary=(rows:BenchmarkSample[])=>({completed:rows.length,passed:rows.filter(r=>r.ok).length,failed:rows.filter(r=>!r.ok).length,
  latency_all:latencySummary(rows.map(r=>r.elapsed_ms)),latency_success:latencySummary(rows.filter(r=>r.ok).map(r=>r.elapsed_ms)),semantic_used:rows.filter(r=>r.semantic_available).length,
  warning_counts:Object.fromEntries([...new Set(rows.flatMap(r=>r.warning_codes))].sort().map(code=>[code,rows.filter(r=>r.warning_codes.includes(code)).length]))});
 return {version:1,workload_id:workload.id,workload_hash:createHash("sha256").update(JSON.stringify(workload)).digest("hex"),corpus_id:workload.corpus_id,options,stopped,complete:samples.length===options.iterations&&stopped==="complete",warmup:summary(warmups),summary:summary(samples),
  measured_wall_ms:wall,throughput_completed_per_second:wall>0?samples.length*1000/wall:0,
  by_probe:Object.fromEntries(workload.probes.map(p=>[p.id,summary(samples.filter(r=>r.probe_id===p.id))])),samples};
}

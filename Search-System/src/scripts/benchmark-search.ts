import "dotenv/config";
import {readFile,stat,writeFile} from "node:fs/promises";
import {resolve} from "node:path";
import {parseBenchmarkWorkload,runSearchBenchmark,DEFAULT_BENCHMARK_OPTIONS,type BenchmarkProbe} from "../performance/search-benchmark.js";
import {readToolApiConfig} from "../config/tool-api.config.js";
try {
 const args=process.argv.slice(2),flags:Record<string,string>={};
 for(let i=0;i<args.length;i+=2){const key=args[i]!;if(!["--workload","--url","--out","--iterations","--concurrency","--warmup","--seconds","--label"].includes(key)||key in flags||args[i+1]===undefined)throw new Error("Invalid benchmark flags");flags[key]=args[i+1]!;}
 if(!flags["--workload"]||!flags["--url"]||!flags["--out"])throw new Error("Require workload URL and output");
 const out=resolve(flags["--out"]),input=resolve(flags["--workload"]);
 if(out===input||await stat(out).then(()=>true,e=>{if(e.code==="ENOENT")return false;throw e;}))throw new Error("Output exists");
 if((await stat(input)).size>256*1024)throw new Error("Workload too large");const bytes=await readFile(input);if(bytes.length>256*1024)throw new Error("Workload too large");
 const workload=parseBenchmarkWorkload(JSON.parse(bytes.toString("utf8"))),base=new URL(flags["--url"]);
 if(base.protocol!=="http:"||!["127.0.0.1","localhost","[::1]"].includes(base.hostname)||base.pathname!=="/"||base.search||base.hash||base.username||base.password)throw new Error("Require loopback API");
 const label=flags["--label"]??"unspecified";if(!/^[a-z0-9_.-]{1,50}$/.test(label))throw new Error("Invalid label");
 const num=(key:string,fallback:number)=>{const text=flags[key];if(text===undefined)return fallback;if(!/^\d+$/.test(text))throw new Error("Invalid numeric bound");return Number(text);};
 const options={...DEFAULT_BENCHMARK_OPTIONS,iterations:num("--iterations",40),concurrency:num("--concurrency",1),warmup:num("--warmup",4),deadlineMs:num("--seconds",120)*1000};
 const api=readToolApiConfig(process.env);if(!api)throw new Error("API token required");
 const call=async(probe:BenchmarkProbe,signal:AbortSignal)=>{
  const path=probe.kind==="search"?"/api/search":"/api/products/"+probe.article_id;
  const r=await fetch(new URL(path,base),{method:probe.kind==="search"?"POST":"GET",redirect:"error",signal,headers:{authorization:"Bearer "+api.token,...(probe.kind==="search"?{"content-type":"application/json"}:{})},...(probe.kind==="search"?{body:JSON.stringify(probe.request)}:{})});
  const reader=r.body?.getReader();if(!reader)throw new Error("Empty response");let length=0;const chunks:Uint8Array[]=[];
  try{while(true){const part=await reader.read();if(part.done)break;length+=part.value.byteLength;if(length>2*1024*1024)throw new Error("Response too large");chunks.push(part.value);}}
  finally{await reader.cancel().catch(()=>{});}
  return {http_status:r.status,body:JSON.parse(Buffer.concat(chunks).toString("utf8"))};
 };
 const start=new Date().toISOString(),report=await runSearchBenchmark(workload,call,options);
 await writeFile(out,JSON.stringify({...report,started_at:start,finished_at:new Date().toISOString(),label,cache_policy:"operator-declared; inspect warning_counts",measurement:"HTTP loopback to existing API; includes database/model/tunnel waits",database_writes:0},null,2)+"\n",{flag:"wx",mode:0o600});
 console.log(JSON.stringify({status:"benchmark_report",complete:report.complete,label,concurrency:options.concurrency,summary:report.summary,throughput_completed_per_second:report.throughput_completed_per_second}));
 if(!report.complete||report.summary.failed||report.warmup.failed)process.exitCode=1;
}catch{console.error(JSON.stringify({status:"benchmark_failed",message:"ตรวจ workload ตัวเลขจำกัด API token และไฟล์รายงานใหม่ ไม่แสดงข้อมูลลับหรือข้อผิดพลาดดิบ"}));process.exitCode=1;}

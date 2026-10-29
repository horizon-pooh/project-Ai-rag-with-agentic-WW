import "dotenv/config";
import {readFile,writeFile,stat} from "node:fs/promises";
import {resolve} from "node:path";
import {evaluateSuite,parseEvaluationSuite} from "../evaluation/search-evaluation.js";
import type {ToolSearchRequest} from "../types/tool-search.types.js";
import {readToolApiConfig} from "../config/tool-api.config.js";
function endpoint(raw:string) {
 const u=new URL(raw);
 if(u.protocol!=="http:"||!["127.0.0.1","localhost","[::1]"].includes(u.hostname)||u.pathname!=="/"||u.search||u.hash||u.username||u.password)throw new Error("Require loopback API URL");
 return u;
}
try {
 const args=process.argv.slice(2),flags:Record<string,string>={};
 for(let i=0;i<args.length;i+=2){const key=args[i]!;if(!["--suite","--keyword-url","--hybrid-url","--out","--limit","--seconds","--split"].includes(key)||key in flags||args[i+1]===undefined)throw new Error("Invalid evaluation flags");flags[key]=args[i+1]!;}
 if(!flags["--suite"]||!flags["--out"]||!flags["--keyword-url"])throw new Error("Require suite output and keyword URL");
 const suitePath=resolve(flags["--suite"]),out=resolve(flags["--out"]);
 if(suitePath===out)throw new Error("Output must differ from suite");
 const exists=await stat(out).then(()=>true,e=>{if(e.code==="ENOENT")return false;throw e;});
 if(exists)throw new Error("Output already exists");
 if((await stat(suitePath)).size>1024*1024)throw new Error("Suite too large");
 const raw=await readFile(suitePath);if(raw.length>1024*1024)throw new Error("Suite too large");
 const suite=parseEvaluationSuite(JSON.parse(raw.toString("utf8")));
 if(suite.target!=="catalogue"||/pending|unreviewed|fixture/i.test(suite.reviewed_by))throw new Error("Live evaluation requires reviewed catalogue labels");
 const limitText=flags["--limit"]??"120",secondsText=flags["--seconds"]??"300",split=flags["--split"]??"all";
 if(!/^[1-9]\d*$/.test(limitText)||Number(limitText)>500||!/^[1-9]\d*$/.test(secondsText)||Number(secondsText)>3600||!["all","development","holdout"].includes(split))throw new Error("Invalid evaluation bounds");
 const config=readToolApiConfig(process.env);if(!config)throw new Error("API token required");
 const keyword=endpoint(flags["--keyword-url"]),hybrid=flags["--hybrid-url"]?endpoint(flags["--hybrid-url"]):null;
 if(hybrid?.href===keyword.href)throw new Error("Require two separate API processes");
 const call=(base:URL)=>async(request:ToolSearchRequest)=>{
  const response=await fetch(new URL("/api/search",base),{method:"POST",redirect:"error",signal:AbortSignal.timeout(20000),headers:{authorization:"Bearer "+config.token,"content-type":"application/json"},body:JSON.stringify(request)});
  if(response.status>=500||response.status===401||response.status===403)throw new Error("Evaluation transport unavailable");
  const reader=response.body?.getReader();if(!reader)throw new Error("Empty response");
  const chunks:Uint8Array[]=[];let bytes=0;
  try{while(true){const part=await reader.read();if(part.done)break;bytes+=part.value.byteLength;if(bytes>2*1024*1024)throw new Error("Response too large");chunks.push(part.value);}}
  finally{await reader.cancel().catch(()=>{});}
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
 };
 const startedAt=new Date().toISOString();
 const report=await evaluateSuite(suite,{keyword:call(keyword),...(hybrid?{hybrid:call(hybrid)}:{})},{limit:Number(limitText),deadlineMs:Number(secondsText)*1000,split:split as "all"|"development"|"holdout"});
 const result={...report,started_at:startedAt,finished_at:new Date().toISOString(),corpus_verified:false,note:"ใช้ corpus_id และป้ายกำกับที่ผู้ตรวจระบุ ต้องหยุด import/backfill ระหว่างเทียบ; คะแนน fixture ไม่ใช่คุณภาพโมเดลจริง"};
 await writeFile(out,JSON.stringify(result,null,2)+"\n",{flag:"wx",mode:0o600});
 console.log(JSON.stringify({status:"evaluation_report",complete:report.complete,selected:report.selected_cases,runs:Object.fromEntries(Object.entries(report.runs).map(([k,v])=>[k,v.summary])),comparison:report.comparison}));
 if(!report.complete||Object.values(report.runs).some(v=>v.summary.passed!==v.summary.cases||v.summary.execution_errors>0))process.exitCode=1;
}catch{
 console.error(JSON.stringify({status:"evaluation_failed",message:"ตรวจ suite ที่ทบทวนแล้ว API สองพอร์ต token และ path รายงานที่ยังไม่มีไฟล์ ไม่แสดงข้อมูลลับหรือข้อผิดพลาดดิบ"}));process.exitCode=1;
}

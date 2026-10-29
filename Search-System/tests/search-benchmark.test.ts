import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile} from "node:fs/promises";
import {latencySummary,parseBenchmarkWorkload,runSearchBenchmark,DEFAULT_BENCHMARK_OPTIONS,type BenchmarkWorkload} from "../src/performance/search-benchmark.js";
const response=JSON.parse(await readFile(new URL("../docs/examples/tool-search-v1/strict-response.json",import.meta.url),"utf8"));
const workload=():BenchmarkWorkload=>({version:1,id:"unit",corpus_id:"fixture",probes:[{id:"search",kind:"search",request:{query:"ทีวี"},expected_status:"ok"}]});
const opts=(extra:Partial<typeof DEFAULT_BENCHMARK_OPTIONS>={})=>({...DEFAULT_BENCHMARK_OPTIONS,iterations:5,warmup:0,...extra});
const reply=()=>({http_status:200,body:structuredClone(response)});
test("p50 p95 ใช้ nearest-rank และไม่แก้ input",()=>{const input=[5,1,4,3,2];assert.deepEqual(latencySummary(input),{samples:5,min_ms:1,p50_ms:3,p95_ms:5,max_ms:5});assert.deepEqual(input,[5,1,4,3,2]);});
test("วัด p95 ของ20ตัวตรงตัวที่19",()=>assert.equal(latencySummary(Array.from({length:20},(_,i)=>i+1)).p95_ms,19));
test("ไม่มี sample เป็น null",()=>assert.equal(latencySummary([]).p95_ms,null));
for(const v of [-1,NaN,Infinity])test("ปฏิเสธ timing "+v,()=>assert.throws(()=>latencySummary([v])));
for(const [name,edit] of [
 ["unknown",(w:any)=>{w.extra=1;}],["empty",(w:any)=>{w.probes=[];}],["duplicate",(w:any)=>{w.probes.push(w.probes[0]);}],
 ["invalid JSON",(w:any)=>{w.probes[0].request.limit="5";}],["unknown path",(w:any)=>{w.probes[0].kind="sql";}],["bad details",(w:any)=>{w.probes=[{id:"detail",kind:"details",article_id:1,expected_status:"ok"}];}]
] as const)test("workload ปฏิเสธ "+name,()=>{const w=workload();edit(w);assert.throws(()=>parseBenchmarkWorkload(w));});
test("workload ถูกคัดลอก",()=>{const w=workload(),copy=parseBenchmarkWorkload(w);w.probes[0]!.id="changed";assert.equal(copy.probes[0]!.id,"search");});
test("warmup ไม่ปน sample และไม่ปน throughput",async()=>{let calls=0;const r=await runSearchBenchmark(workload(),async()=>{calls++;return reply();},opts({iterations:5,warmup:2}));assert.equal(calls,7);assert.equal(r.warmup.completed,2);assert.equal(r.summary.completed,5);assert.equal(r.summary.passed,5);assert.ok(r.throughput_completed_per_second>0);});
test("concurrency จำกัดงานและไม่ข้ามหรือทำซ้ำ sequence",async()=>{let active=0,max=0;const r=await runSearchBenchmark(workload(),async()=>{active++;max=Math.max(max,active);await new Promise(r=>setTimeout(r,2));active--;return reply();},opts({iterations:12,concurrency:3}));assert.equal(max,3);assert.equal(r.complete,true);assert.deepEqual(r.samples.map(s=>s.sequence),Array.from({length:12},(_,i)=>i));});
test("คำตอบผิดรวมใน latency_all แต่ไม่รวม latency_success",async()=>{let n=0;const r=await runSearchBenchmark(workload(),async()=>++n===1?{http_status:503,body:{}}:reply(),opts());assert.equal(r.summary.failed,1);assert.equal(r.summary.latency_all.samples,5);assert.equal(r.summary.latency_success.samples,4);});
test("network error ไม่เปิดเผยข้อความลับและไม่เป็น no_matches",async()=>{const r=await runSearchBenchmark(workload(),async()=>{throw Error("private-password");},opts());assert.equal(r.summary.failed,5);assert.ok(!JSON.stringify(r).includes("private-password"));assert.ok(r.samples.every(s=>s.status==="execution_error"));});
test("สัญญาถูกแต่สถานะผิดยังไม่ผ่าน",async()=>{const w=workload();w.probes[0]!.expected_status="no_matches";const r=await runSearchBenchmark(w,async()=>reply(),opts());assert.equal(r.summary.failed,5);});
test("คำขอแต่ละรอบไม่แก้ต้นฉบับ",async()=>{const w=workload();await runSearchBenchmark(w,async p=>{if(p.kind==="search")p.request.query="changed";return reply();},opts());assert.equal(w.probes[0]!.kind==="search"?w.probes[0]!.request.query:null,"ทีวี");});
test("timeout จบได้แม้ transport ไม่ยอมตอบ โดยไม่สร้างงานเพิ่ม",async()=>{let calls=0,aborted=false;const r=await runSearchBenchmark(workload(),async(_,signal)=>{calls++;signal.addEventListener("abort",()=>{aborted=true;});return new Promise(()=>{});},opts({iterations:10,concurrency:2,requestTimeoutMs:10}));assert.equal(r.complete,false);assert.equal(r.stopped,"timeout");assert.equal(calls,2);assert.equal(aborted,true);assert.equal(r.summary.failed,2);});
test("budget จำกัดทั้ง warmup และรอบวัด",async()=>{const r=await runSearchBenchmark(workload(),async()=>{await new Promise(r=>setTimeout(r,10));return reply();},opts({deadlineMs:1,warmup:4}));assert.equal(r.complete,false);assert.equal(r.summary.completed,0);});
test("นับ semantic และ warning ตามคำตอบจริง ไม่ตาม label",async()=>{const b=structuredClone(response);b.semantic_available=false;const r=await runSearchBenchmark(workload(),async()=>({http_status:200,body:b}),opts());assert.equal(r.summary.semantic_used,0);assert.ok(Object.keys(r.summary.warning_counts).length>0);});
test("รายละเอียด draft คาดหมาย404 ไม่นับเป็นความล้มเหลว",async()=>{const w=workload();w.probes=[{id:"draft",kind:"details",article_id:106045,expected_status:"not_found"}];const r=await runSearchBenchmark(w,async()=>({http_status:404,body:{status:"not_found"}}),opts());assert.equal(r.summary.passed,5);});
for(const extra of [{iterations:301},{concurrency:0},{concurrency:11},{warmup:21},{deadlineMs:0},{requestTimeoutMs:20001}])test("ปฏิเสธขอบเขต "+JSON.stringify(extra),async()=>assert.rejects(runSearchBenchmark(workload(),async()=>reply(),opts(extra))));

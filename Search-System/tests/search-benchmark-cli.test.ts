import assert from "node:assert/strict";
import {test} from "node:test";
import {createServer} from "node:http";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {mkdtemp,readFile,writeFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
const exec=promisify(execFile),token="test-only-token-123456789012345678901234567890";
const body=JSON.parse(await readFile(new URL("../docs/examples/tool-search-v1/strict-response.json",import.meta.url),"utf8"));
test("benchmark CLI ใช้ HTTP ที่จำกัดและไม่เปิดเผย token",async t=>{
 const dir=await mkdtemp(join(tmpdir(),"benchmark-cli-")),file=join(dir,"workload.json"),out=join(dir,"report.json");
 let status=200,count=0;
 const server=createServer((req,res)=>{count++;assert.equal(req.url,"/api/search");assert.equal(req.headers.authorization,"Bearer "+token);req.resume();res.writeHead(status,{"content-type":"application/json",location:"http://example.invalid"});res.end(status===200?JSON.stringify(body):JSON.stringify({private_error:token}));});
 await new Promise<void>(resolve=>server.listen(0,"127.0.0.1",resolve));const address=server.address();if(!address||typeof address==="string")throw Error();const url="http://127.0.0.1:"+address.port;
 await writeFile(file,JSON.stringify({version:1,id:"cli",corpus_id:"fixture",probes:[{id:"search",kind:"search",request:{query:"ทีวี"},expected_status:"ok"}]}));
 const run=async(extra:string[]=[],target=out)=>{try{const r=await exec(process.execPath,["--import","tsx","src/scripts/benchmark-search.ts","--workload",file,"--url",url,"--out",target,"--iterations","5","--warmup","2",...extra],{env:{...process.env,SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:token},maxBuffer:1024*1024});return {code:0,text:r.stdout+r.stderr};}catch(e){const r=e as {code:number;stdout:string;stderr:string};return {code:r.code,text:r.stdout+r.stderr};}};
 try{
  await t.test("ได้ sample ตามจำนวนและแยก warmup",async()=>{const r=await run(["--concurrency","2"]);assert.equal(r.code,0,r.text);assert.ok(!r.text.includes(token));const report=JSON.parse(await readFile(out,"utf8"));assert.equal(report.summary.completed,5);assert.equal(report.warmup.completed,2);assert.equal(report.summary.semantic_used,0);});
  await t.test("ไฟล์มีอยู่แล้วไม่เรียก API ซ้ำ",async()=>{const before=count;assert.equal((await run()).code,1);assert.equal(count,before);});
  await t.test("ไม่ส่ง token ไป URL ภายนอก",async()=>{const before=count;assert.equal((await run(["--url","http://example.invalid"],join(dir,"remote.json"))).code,1);assert.equal(count,before);});
  await t.test("concurrency เกินขอบเขตไม่เริ่มยิงคำขอ",async()=>{const before=count;assert.equal((await run(["--concurrency","11"],join(dir,"too-many.json"))).code,1);assert.equal(count,before);});
  await t.test("HTTP503 เก็บเป็น failed ไม่ซ่อนใน p95 สำเร็จ",async()=>{status=503;const r=await run([],join(dir,"failed.json"));assert.equal(r.code,1);assert.ok(!r.text.includes(token));const report=JSON.parse(await readFile(join(dir,"failed.json"),"utf8"));assert.equal(report.summary.failed,5);assert.equal(report.summary.latency_success.samples,0);});
  await t.test("redirect ไม่ถูกตาม",async()=>{status=302;assert.equal((await run([],join(dir,"redirect.json"))).code,1);});
 }finally{await new Promise<void>((resolve,reject)=>server.close(e=>e?reject(e):resolve()));await rm(dir,{recursive:true,force:true});}
});

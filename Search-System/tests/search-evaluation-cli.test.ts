import assert from "node:assert/strict";
import {test} from "node:test";
import {createServer,type Server} from "node:http";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {mkdtemp,readFile,writeFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
const exec=promisify(execFile),token="test-only-token-123456789012345678901234567890";
const response=JSON.parse(await readFile(new URL("../docs/examples/tool-search-v1/strict-response.json",import.meta.url),"utf8"));
const suite=()=>({version:1,id:"cli",target:"catalogue",corpus_id:"test-only",label_source:"test labels",reviewed_by:"test-review",forbidden_articles:[],cases:[{id:"A",tags:["test"],split:"development",request:{query:"ทีวี"},expected:{status:"ok"}}]});
const listen=(server:Server)=>new Promise<string>(resolve=>server.listen(0,"127.0.0.1",()=>{const a=server.address();if(!a||typeof a==="string")throw Error();resolve(`http://127.0.0.1:${a.port}`);}));
const close=(server:Server)=>new Promise<void>((resolve,reject)=>server.close(e=>e?reject(e):resolve()));
test("CLI จำกัด transport ป้องกัน secrets และไม่ทับรายงานเดิม",async t=>{
 const dir=await mkdtemp(join(tmpdir(),"evaluation-cli-"));let status=200,body=JSON.stringify(response),requests=0;
 const make=()=>createServer((req,res)=>{requests++;assert.equal(req.headers.authorization,"Bearer "+token);assert.equal(req.url,"/api/search");req.resume();res.writeHead(status,{"content-type":"application/json",location:"http://example.invalid"});res.end(body);});
 const a=make(),b=make(),u=await listen(a),v=await listen(b);
 const file=join(dir,"suite.json"),out=join(dir,"report.json");
 const run=async(extra:string[]=[],target=out)=>{await writeFile(file,JSON.stringify(suite()));try{const r=await exec(process.execPath,["--import","tsx","src/scripts/evaluate-search.ts","--suite",file,"--keyword-url",u,"--hybrid-url",v,"--out",target,...extra],{env:{...process.env,SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:token},maxBuffer:1024*1024});return {code:0,text:r.stdout+r.stderr};}catch(e){const r=e as {code:number;stdout:string;stderr:string};return {code:r.code,text:r.stdout+r.stderr};}};
 try{
  await t.test("สองพอร์ตได้รายงานเทียบจริง ไม่พิมพ์ token",async()=>{const r=await run();assert.equal(r.code,0,r.text);assert.ok(!r.text.includes(token));const report=JSON.parse(await readFile(out,"utf8"));assert.equal(report.comparison.paired_cases,1);assert.equal(report.corpus_verified,false);assert.equal(report.runs.keyword.summary.cases,1);});
  await t.test("ไม่เขียนทับรายงาน",async()=>{const before=await readFile(out,"utf8"),count=requests;assert.equal((await run()).code,1);assert.equal(await readFile(out,"utf8"),before);assert.equal(requests,count);});
  await t.test("API พอร์ตเดียวกันถูกปฏิเสธ",async()=>assert.equal((await run(["--hybrid-url",u],join(dir,"same.json"))).code,1));
  await t.test("ฐานผิดพลาดไม่เป็น no_matches",async()=>{status=503;body=JSON.stringify({private_error:token});const r=await run([],join(dir,"error.json"));assert.equal(r.code,1);assert.ok(!r.text.includes(token));const report=JSON.parse(await readFile(join(dir,"error.json"),"utf8"));assert.equal(report.runs.keyword.summary.execution_errors,1);});
  await t.test("redirect ไม่ส่ง token ต่อ",async()=>{status=302;body="";assert.equal((await run([],join(dir,"redirect.json"))).code,1);});
  await t.test("JSON พังเป็น execution_error",async()=>{status=200;body="not json";assert.equal((await run([],join(dir,"json-error.json"))).code,1);});
  await t.test("fixture suite ไม่ถูกส่งไป live API",async()=>{const s=suite();s.target="fixture";await writeFile(file,JSON.stringify(s));const count=requests;await assert.rejects(exec(process.execPath,["--import","tsx","src/scripts/evaluate-search.ts","--suite",file,"--keyword-url",u,"--out",join(dir,"fixture.json")],{env:{...process.env,SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:token}}));assert.equal(requests,count);});
 }finally{await Promise.all([close(a),close(b)]);await rm(dir,{recursive:true,force:true});}
});

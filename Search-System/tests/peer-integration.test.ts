import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile} from "node:fs/promises";
import {createServer} from "node:http";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {privateApiUrl,boundedJson} from "../src/deployment/private-http.js";
import {checkPeerIntegration,type PeerCall} from "../src/deployment/peer-check.js";
const exec=promisify(execFile),token="test-only-token-123456789012345678901234567890";
const raw=JSON.parse(await readFile(new URL("../docs/examples/tool-search-v1/strict-response.json",import.meta.url),"utf8"));
const search=structuredClone(raw);for(const r of search.results)r.product.category_id="AP0101";search.resolved_categories=["AP0101"];
const invalid={version:"1",request_id:"test",status:"invalid_request",issues:[{path:"/limit",code:"type",message:"Invalid limit"}]};
const clarification={version:"1",request_id:"test",status:"needs_clarification",clarifications:[{path:"/filters",reason:"category_scope_required",candidates:[]}]};
const details={version:"1",request_id:"test",status:"ok",details:{product:search.results[0].product,name_en:null,series:null,short_description:"fixture",regular_price:null,sale_unit:"ชิ้น",pack_quantity:1,stock_quantity:1,specifications:[],feature_details:[],content_sections:[]}};
const call:PeerCall=async(path,body,authorized=true)=>{
 if(path==="/health")return {http_status:200,body:{status:"ok",service:"search-system"}};
 if(!authorized)return {http_status:401,body:{status:"unauthorized"}};
 if(path==="/api/products/106045")return {http_status:404,body:{status:"not_found"}};
 if(path.startsWith("/api/products/"))return {http_status:200,body:details};
 const b=body as any;if(b.limit==="5")return {http_status:400,body:invalid};if(!b.filters)return {http_status:200,body:clarification};
 const s=structuredClone(search);if(b.exact){s.results=s.results.slice(0,1);s.meta.returned_count=1;s.meta.total_matches=1;s.meta.has_more=false;}return {http_status:200,body:s};
};
for(const url of ["http://example.com","http://8.8.8.8:3000","http://100.63.0.1:3000","http://name:pass@127.0.0.1","http://127.0.0.1/path","http://127.0.0.1/?token=x"])test("peer URL ปฏิเสธ "+url,()=>assert.throws(()=>privateApiUrl(url)));
test("peer ใช้ URL Tailscale โดยไม่ต้องมีรหัสฐาน",()=>assert.equal(privateApiUrl("http://100.116.20.59:3000").hostname,"100.116.20.59"));
test("response เกินขนาดถูกยกเลิก",async()=>assert.rejects(boundedJson(new Response("x".repeat(9000)),8192)));
test("peer ตรวจครบทั้ง route และสถานะ",async()=>{const r=await checkPeerIntegration(call);assert.equal(r.status,"peer_integration_ready");assert.equal(r.checks.length,8);assert.equal(r.database_credentials_required,false);});
test("draft หลุดหรือ auth ไม่ทำงานต้องไม่รายงานพร้อม",async()=>{await assert.rejects(checkPeerIntegration(async(p,b,a)=>p==="/api/products/106045"?{http_status:200,body:details}:call(p,b,a)));await assert.rejects(checkPeerIntegration(async(p,b,a)=>a===false&&p!=="/health"?{http_status:200,body:search}:call(p,b,a)));});
test("CLI peer ทำงานจาก process ที่ไม่มี database env",async()=>{
 const server=createServer(async(req,res)=>{let text="";for await(const c of req)text+=c;const r=await call(req.url!,text?JSON.parse(text):undefined,req.headers.authorization==="Bearer "+token);res.writeHead(r.http_status,{"content-type":"application/json"});res.end(JSON.stringify(r.body));});
 await new Promise<void>(r=>server.listen(0,"127.0.0.1",r));const address=server.address();if(!address||typeof address==="string")throw Error();
 try{const env:NodeJS.ProcessEnv={...process.env,DATA_SOURCE:"mock",SEARCH_API_TOKEN:token};delete env.SEARCH_DATABASE_URL;delete env.SEARCH_DATABASE_PASSWORD;const r=await exec(process.execPath,["--import","tsx","src/scripts/check-peer-api.ts","--url","http://127.0.0.1:"+address.port],{env,maxBuffer:1024*1024});assert.ok(r.stdout.includes("peer_integration_ready"));assert.ok(!r.stdout.includes(token));}finally{await new Promise<void>((r,j)=>server.close(e=>e?j(e):r()));}
});

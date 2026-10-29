import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile} from "node:fs/promises";
import {evaluateSuite,parseEvaluationSuite,rankingMetrics,scoreCase,suiteHash,type EvaluationSuite,type EvaluationCase} from "../src/evaluation/search-evaluation.js";
const response=JSON.parse(await readFile(new URL("../docs/examples/tool-search-v1/strict-response.json",import.meta.url),"utf8"));
const good=():EvaluationCase=>({id:"A",tags:["test"],split:"development",request:{query:"ทีวี"},expected:{status:"ok"}});
const suite=():EvaluationSuite=>({version:1,id:"test",target:"fixture",corpus_id:"fixture-v1",label_source:"independent labels",reviewed_by:"fixture-specification",forbidden_articles:[],cases:[good()]});
test("metrics ใช้สูตรที่ตรวจด้วยมือได้",()=>{
 const m=rankingMetrics([100001,100000,100002],{"100000":3,"100001":0,"100002":1},3);
 assert.equal(m.precision_at_k,2/3);assert.equal(m.recall_at_k,1);assert.equal(m.mrr_at_k,0.5);
 assert.ok(Math.abs(m.ndcg_at_k-((7/Math.log2(3)+1/2)/(7+1/Math.log2(3))))<1e-12);
});
test("อันดับดีที่สุดได้ nDCG 1",()=>assert.equal(rankingMetrics([100000,100001],{"100000":3,"100001":1},5).ndcg_at_k,1));
test("จำนวนผลน้อยกว่า k ไม่ทำ precision สูงเกินจริง",()=>assert.equal(rankingMetrics([100000],{"100000":3},5).precision_at_k,0.2));
test("ไม่พบผลที่เกี่ยวข้องได้ศูนย์ ไม่ใช่ error",()=>assert.equal(rankingMetrics([100001],{"100000":3,"100001":0}).mrr_at_k,0));
test("ผลไม่มีป้ายกำกับไม่นับเป็นไม่เกี่ยวข้อง",()=>{const m=rankingMetrics([100001],{"100000":3});assert.equal(m.ndcg_at_k,null);assert.equal(m.unjudged,1);});
test("ไม่มี positive labels ไม่สร้างคะแนน",()=>assert.equal(rankingMetrics([100000],{"100000":0}).recall_at_k,null));
test("คัดลอก suite และ hash เปลี่ยนเมื่อแก้เฉลย",()=>{const s=suite(),parsed=parseEvaluationSuite(s),hash=suiteHash(parsed);s.cases[0]!.expected.status="no_matches";assert.equal(parsed.cases[0]!.expected.status,"ok");assert.notEqual(suiteHash(s),hash);});
for(const [name,edit] of [
 ["unknown field",(s:any)=>{s.extra=true;}],["duplicate ID",(s:any)=>{s.cases.push({...good(),request:{query:"OLED"}});}],["duplicate request",(s:any)=>{s.cases.push({...good(),id:"B"});}],
 ["bad grade",(s:any)=>{s.cases[0].expected.ranking={complete:true,grades:{"100000":4}};}],["empty grades",(s:any)=>{s.cases[0].expected.ranking={complete:true,grades:{}};}],
 ["bad Article",(s:any)=>{s.forbidden_articles=[999999];}],["empty tags",(s:any)=>{s.cases[0].tags=[];}]
] as const)test("suite ปฏิเสธ "+name,()=>{const s=suite();edit(s);assert.throws(()=>parseEvaluationSuite(s));});
test("contract ผิดไม่นับเป็นคำตอบผ่าน",()=>assert.deepEqual(scoreCase(good(),{}).failures,["response_contract"]));
test("status ผิดถูกบันทึก",()=>{const c=good();c.expected.status="no_matches";assert.ok(scoreCase(c,response).failures.includes("status"));});
test("draft หรือ Article ต้องห้ามทำให้ไม่ผ่าน",()=>assert.ok(scoreCase(good(),response,[response.results[0].product.article_id]).failures.includes("forbidden_article")));
test("category scope ประเมินเซต ไม่ยึดลำดับ",()=>{const c=good();c.expected.category_ids=[...response.resolved_categories].reverse();assert.equal(scoreCase(c,response).category!.exact,true);});
test("strict ไม่ปะปน alternatives",()=>{const c=good();c.expected.strict_ids=[];assert.ok(scoreCase(c,response).failures.includes("strict_set"));});
test("filter ตรวจ canonical value/unit และ path",()=>{const c=good(),f=response.applied_filters[0];c.expected.applied=[{path:f.path,resolved_id:f.resolved_id,value:f.value,unit:f.unit}];assert.equal(scoreCase(c,response).filter_correct,1);c.expected.applied[0]!.unit="bad-unit";assert.equal(scoreCase(c,response).filter_correct,0);});
test("qrels ไม่ครบไม่คำนวณ ranking",()=>{const c=good();c.expected.ranking={complete:false,grades:{"100000":1}};assert.equal(scoreCase(c,response).ranking,null);});
test("page ถัดไปไม่คำนวณ metric ของหน้าแรก",()=>{const c=good();c.request.page=2;c.expected.ranking={complete:true,grades:{"100000":1}};assert.equal(scoreCase(c,response).ranking,null);});
test("สองเครื่องมือได้รับ request สำเนาคนละตัว",async()=>{const s=suite();let seen="";const r=await evaluateSuite(s,{keyword:async input=>{input.query="mutated";return response;},hybrid:async input=>{seen=input.query!;return response;}});assert.equal(seen,"ทีวี");assert.equal(s.cases[0]!.request.query,"ทีวี");assert.equal(r.comparison.paired_cases,1);});
test("engine ล่มไม่กลายเป็น no_matches และไม่หลุดจาก denominator",async()=>{const s=suite();s.cases[0]!.expected.category_ids=["TV0108"];s.cases[0]!.expected.applied=[{path:"/x",resolved_id:null,value:0,unit:null}];const r=await evaluateSuite(s,{keyword:async()=>{throw new Error("private secret");}});assert.equal(r.runs.keyword!.summary.execution_errors,1);assert.equal(r.runs.keyword!.summary.category.recall,0);assert.equal(r.runs.keyword!.summary.filters.accuracy,0);assert.ok(!JSON.stringify(r).includes("private secret"));});
test("holdout แยกและไม่เปลี่ยน suite",async()=>{const s=suite();s.cases.push({...good(),id:"B",split:"holdout",request:{query:"OLED"}});const r=await evaluateSuite(s,{keyword:async()=>response},{split:"holdout"});assert.equal(r.selected_cases,1);assert.equal(r.runs.keyword!.cases[0]!.id,"B");});
test("budget หยุดพร้อมรายงาน incomplete",async()=>{const s=suite();s.cases.push({...good(),id:"B",request:{query:"OLED"}});const r=await evaluateSuite(s,{keyword:async()=>{await new Promise(r=>setTimeout(r,10));return response;}},{deadlineMs:1});assert.equal(r.complete,false);assert.equal(r.stopped,"budget");});
for(const options of [{limit:0},{limit:501},{k:0},{deadlineMs:0}])test("ปฏิเสธขอบเขตผิด "+JSON.stringify(options),async()=>{await assert.rejects(evaluateSuite(suite(),{keyword:async()=>response},options));});

test("request เดียวกันที่สลับลำดับ key ยังเป็น duplicate",()=>{const s=suite();s.cases[0]!.request={query:"ทีวี",limit:5};s.cases.push({...good(),id:"B",request:{limit:5,query:"ทีวี"}});assert.throws(()=>parseEvaluationSuite(s));});

test("filter ของอีกหมวดหรือ operator คนละตัวไม่นับว่าจับคู่ถูก",()=>{const c=good(),f=response.applied_filters[0];c.expected.applied=[{path:f.path,resolved_id:f.resolved_id,value:f.value,unit:f.unit,category_id:"AP9999"}];assert.equal(scoreCase(c,response).filter_correct,0);c.expected.applied[0]!.category_id=f.category_id;c.expected.applied[0]!.op=f.op==="eq"?"gte":"eq";assert.equal(scoreCase(c,response).filter_correct,0);});

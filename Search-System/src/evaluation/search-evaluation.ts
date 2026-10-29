import {Ajv} from "ajv";
import {createHash} from "node:crypto";
import {validateToolSearchResponse} from "../validation/tool-search.validation.js";
import type {ToolSearchResponse,ToolSearchRequest,AppliedFilter} from "../types/tool-search.types.js";
export interface EvaluationCase {
 id:string;tags:string[];split:"development"|"holdout";request:ToolSearchRequest;
 expected:{status:ToolSearchResponse["status"];category_ids?:string[];strict_ids?:number[];top_ids?:number[];
  applied?:Array<Pick<AppliedFilter,"path"|"resolved_id"|"value"|"unit"> & Partial<Pick<AppliedFilter,"category_id"|"op">>>;unapplied_paths?:string[];issue_paths?:string[];
  ranking?:{complete:boolean;grades:Record<string,number>}};
}
export interface EvaluationSuite {version:1;id:string;target:"fixture"|"catalogue";corpus_id:string;label_source:string;reviewed_by:string;
 forbidden_articles:number[];cases:EvaluationCase[]}
const obj=(properties:Record<string,unknown>,required=Object.keys(properties))=>({type:"object",properties,required,additionalProperties:false});
const texts={type:"array",items:{type:"string",minLength:1,maxLength:200},uniqueItems:true,maxItems:2000};
const ids={type:"array",items:{type:"integer",minimum:100000,maximum:199999},uniqueItems:true,maxItems:100};
const value={anyOf:[{type:"string",maxLength:2000},{type:"number"},{type:"boolean"},{type:"null"},{type:"array",items:{anyOf:[{type:"string"},{type:"number"},{type:"boolean"}]},maxItems:100}]};
const expectation=obj({status:{enum:["ok","no_matches","needs_clarification","constraint_conflict","invalid_request","search_unavailable"]},
 category_ids:texts,strict_ids:ids,top_ids:ids,applied:{type:"array",maxItems:100,items:obj({path:{type:"string",maxLength:200},resolved_id:{type:["string","null"]},value,unit:{type:["string","null"]},category_id:{type:["string","null"]},op:{enum:["eq","gte","lte","in"]}},["path","resolved_id","value","unit"])},
 unapplied_paths:texts,issue_paths:texts,ranking:obj({complete:{type:"boolean"},grades:{type:"object",maxProperties:1000,propertyNames:{pattern:"^1[0-9]{5}$"},additionalProperties:{type:"integer",minimum:0,maximum:3}}})},["status"]);
const schema=obj({version:{const:1},id:{type:"string",pattern:"^[A-Za-z0-9_.-]{1,80}$"},target:{enum:["fixture","catalogue"]},
 corpus_id:{type:"string",minLength:1,maxLength:200},label_source:{type:"string",minLength:1,maxLength:1000},reviewed_by:{type:"string",minLength:1,maxLength:200},
 forbidden_articles:ids,cases:{type:"array",minItems:1,maxItems:500,items:obj({id:{type:"string",pattern:"^[A-Za-z0-9_.-]{1,80}$"},tags:texts,split:{enum:["development","holdout"]},request:{type:"object"},expected:expectation})}});
const valid=new Ajv({strict:true,strictNumbers:true,allowUnionTypes:true}).compile(schema);
const canonical=(value:unknown):unknown=>Array.isArray(value)?value.map(canonical):value && typeof value==="object"?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,canonical(v)])):value;
export function parseEvaluationSuite(raw:unknown):EvaluationSuite {
 if(!valid(raw))throw new Error("Invalid evaluation suite");
 const suite=structuredClone(raw) as unknown as EvaluationSuite;
 if(new Set(suite.cases.map(c=>c.id)).size!==suite.cases.length)throw new Error("Duplicate case ID");
 if(new Set(suite.cases.map(c=>JSON.stringify(canonical(c.request)))).size!==suite.cases.length)throw new Error("Duplicate request");
 if(suite.cases.some(c=>!c.tags.length || (c.expected.ranking && (!Object.keys(c.expected.ranking.grades).length || !Object.values(c.expected.ranking.grades).some(v=>v>0)))))throw new Error("Missing evaluation labels");
 return suite;
}
export const suiteHash=(suite:EvaluationSuite)=>createHash("sha256").update(JSON.stringify(suite)).digest("hex");
const sorted=(v:unknown[])=>v.map(x=>JSON.stringify(x)).sort();
const sameSet=(a:unknown[],b:unknown[])=>JSON.stringify(sorted(a))===JSON.stringify(sorted(b));
export function rankingMetrics(ids:number[],grades:Record<string,number>,k=5) {
 const top=ids.slice(0,k),positive=Object.values(grades).filter(g=>g>0).length;
 const dcg=(g:number[])=>g.reduce((s,x,i)=>s+(2**x-1)/Math.log2(i+2),0);
 const ideal=dcg(Object.values(grades).sort((a,b)=>b-a).slice(0,k));
 const unjudged=top.filter(id=>!(String(id) in grades)).length;
 if(unjudged || !positive || !ideal)return {precision_at_k:null,recall_at_k:null,mrr_at_k:null,ndcg_at_k:null,unjudged};
 const relevant=top.filter(id=>grades[String(id)]!>0).length,first=top.findIndex(id=>grades[String(id)]!>0);
 return {precision_at_k:relevant/k,recall_at_k:relevant/positive,mrr_at_k:first<0?0:1/(first+1),ndcg_at_k:dcg(top.map(id=>grades[String(id)]!))/ideal,unjudged};
}
export interface CaseScore {
 id:string;tags:string[];split:EvaluationCase["split"];status:string;passed:boolean;failures:string[];
 category:{tp:number;predicted:number;expected:number;exact:boolean}|null;
 filter_correct:number;filter_total:number;strict_set_exact:boolean|null;ranking:ReturnType<typeof rankingMetrics>|null;
 semantic_available:boolean;warning_codes:string[];articles:number[];strict_articles:number[];
}
export function scoreCase(c:EvaluationCase,response:unknown,forbidden:number[]=[],k=5):CaseScore {
 const failures:string[]=[];
 const base:CaseScore={id:c.id,tags:c.tags,split:c.split,status:"invalid_response",passed:false,failures,category:c.expected.category_ids?{tp:0,predicted:0,expected:c.expected.category_ids.length,exact:false}:null,filter_correct:0,filter_total:(c.expected.applied?.length??0)+(c.expected.unapplied_paths?.length??0)+(c.expected.issue_paths?.length??0),strict_set_exact:c.expected.strict_ids?false:null,ranking:null,semantic_available:false,warning_codes:[],articles:[],strict_articles:[]};
 if(!validateToolSearchResponse(response).valid){failures.push("response_contract");return base;}
 const r=response as ToolSearchResponse;base.status=r.status;if(r.status!==c.expected.status)failures.push("status");
 const success=r.status==="ok" || r.status==="no_matches";
 if(success){base.semantic_available=r.semantic_available;base.warning_codes=r.warnings.map(w=>w.code);base.articles=r.results.map(x=>x.product.article_id);base.strict_articles=r.results.filter(x=>x.match_type==="matches_applied_filters").map(x=>x.product.article_id);
  if(base.articles.some(id=>forbidden.includes(id)))failures.push("forbidden_article");
 }
 const actualCategories=success?r.resolved_categories:[];
 if(c.expected.category_ids){const e=c.expected.category_ids,p=actualCategories;base.category={tp:p.filter(x=>e.includes(x)).length,predicted:p.length,expected:e.length,exact:sameSet(p,e)};if(!base.category.exact)failures.push("category_scope");}
 if(c.expected.strict_ids){base.strict_set_exact=sameSet(base.strict_articles,c.expected.strict_ids);if(!base.strict_set_exact)failures.push("strict_set");}
 if(c.expected.top_ids && JSON.stringify(base.articles.slice(0,c.expected.top_ids.length))!==JSON.stringify(c.expected.top_ids))failures.push("top_order");
 for(const expected of c.expected.applied??[]){if(success && r.applied_filters.some(f=>JSON.stringify([f.path,f.resolved_id,f.value,f.unit])===JSON.stringify([expected.path,expected.resolved_id,expected.value,expected.unit])&&(expected.category_id===undefined||f.category_id===expected.category_id)&&(expected.op===undefined||f.op===expected.op)))base.filter_correct++;else failures.push("applied:"+expected.path);}
 for(const path of c.expected.unapplied_paths??[]){if(success && r.unapplied_filters.some(f=>f.path===path))base.filter_correct++;else failures.push("unapplied:"+path);}
 const issues=success?[]:r.status==="needs_clarification"?r.clarifications:"issues" in r?r.issues:[];
 for(const path of c.expected.issue_paths??[]){if(issues.some(f=>f.path===path))base.filter_correct++;else failures.push("issue:"+path);}
 if(c.expected.ranking?.complete && success && (c.request.page??1)===1 && (c.request.limit??5)>=k)base.ranking=rankingMetrics(base.articles,c.expected.ranking.grades,k);
 base.passed=failures.length===0;return base;
}
function aggregate(rows:CaseScore[]) {
 const cats=rows.flatMap(r=>r.category?[r.category]:[]),tp=cats.reduce((n,c)=>n+c.tp,0),pred=cats.reduce((n,c)=>n+c.predicted,0),expected=cats.reduce((n,c)=>n+c.expected,0);
 const filterTotal=rows.reduce((n,c)=>n+c.filter_total,0),filterCorrect=rows.reduce((n,c)=>n+c.filter_correct,0);
 const ranked=rows.flatMap(r=>r.ranking?.ndcg_at_k!==null && r.ranking?[r.ranking]:[]);
 const mean=(key:"precision_at_k"|"recall_at_k"|"mrr_at_k"|"ndcg_at_k")=>ranked.length?ranked.reduce((n,r)=>n+r[key]!,0)/ranked.length:null;
 return {cases:rows.length,passed:rows.filter(r=>r.passed).length,execution_errors:rows.filter(r=>r.status==="execution_error").length,
 category:{cases:cats.length,exact:cats.filter(c=>c.exact).length,precision:pred?tp/pred:null,recall:expected?tp/expected:null},
 filters:{correct:filterCorrect,total:filterTotal,accuracy:filterTotal?filterCorrect/filterTotal:null},
 product_filters:{cases:rows.filter(r=>r.strict_set_exact!==null).length,exact:rows.filter(r=>r.strict_set_exact===true).length},
 ranking:{judged_cases:ranked.length,unjudged_results:rows.reduce((n,r)=>n+(r.ranking?.unjudged??0),0),precision_at_k:mean("precision_at_k"),recall_at_k:mean("recall_at_k"),mrr_at_k:mean("mrr_at_k"),ndcg_at_k:mean("ndcg_at_k")},
 semantic_used:rows.filter(r=>r.semantic_available).length};
}
export async function evaluateSuite(suite:EvaluationSuite,engines:Record<string,(request:ToolSearchRequest)=>Promise<unknown>>,options:{limit?:number;k?:number;split?:"development"|"holdout"|"all";deadlineMs?:number}={}) {
 const limit=options.limit??suite.cases.length,k=options.k??5,deadlineMs=options.deadlineMs??300000;
 if(!Number.isInteger(limit)||limit<1||limit>500||!Number.isInteger(k)||k<1||k>100||!Number.isInteger(deadlineMs)||deadlineMs<1||deadlineMs>3600000)throw new Error("Invalid evaluation bound");
 const cases=suite.cases.filter(c=>!options.split||options.split==="all"||c.split===options.split).slice(0,limit);
 const runs:Record<string,CaseScore[]>={};for(const name of Object.keys(engines))runs[name]=[];
 const started=performance.now();let stopped="complete";
 // แต่ละกรณีสลับเครื่องมือบนคำขอเดียวกัน ไม่ขนานโหลดฐานและโมเดล
 outer:for(const c of cases)for(const [name,engine] of Object.entries(engines)){
  if(performance.now()-started>=deadlineMs){stopped="budget";break outer;}
  try{runs[name]!.push(scoreCase(c,await engine(structuredClone(c.request)),suite.forbidden_articles,k));}
  catch{const failed=scoreCase(c,null,suite.forbidden_articles,k);failed.status="execution_error";failed.failures=["execution_error"];runs[name]!.push(failed);}
 }
 const entries=Object.entries(runs),paired=entries.length===2?entries[0]![1].map(a=>{const b=entries[1]![1].find(x=>x.id===a.id);return b?{a,b}:null;}).filter(x=>x!==null):[];
 const rankPairs=paired.filter(x=>x.a.ranking?.ndcg_at_k!=null&&x.b.ranking?.ndcg_at_k!=null&&x.b.semantic_available);
 return {version:1,suite_id:suite.id,suite_hash:suiteHash(suite),target:suite.target,corpus_id:suite.corpus_id,label_source:suite.label_source,reviewed_by:suite.reviewed_by,
 k,split:options.split??"all",selected_cases:cases.length,ranking_label_cases:cases.filter(c=>c.expected.ranking).length,stopped,
 runs:Object.fromEntries(entries.map(([name,rows])=>[name,{summary:aggregate(rows),by_split:{development:aggregate(rows.filter(r=>r.split==="development")),holdout:aggregate(rows.filter(r=>r.split==="holdout"))},by_tag:Object.fromEntries([...new Set(rows.flatMap(x=>x.tags))].sort().map(tag=>[tag,aggregate(rows.filter(r=>r.tags.includes(tag)))])),cases:rows}])),
 comparison:{engines:entries.map(([n])=>n),paired_cases:paired.length,hybrid_ranked_pairs:rankPairs.length,mean_ndcg_delta:rankPairs.length?rankPairs.reduce((n,x)=>n+x.b.ranking!.ndcg_at_k!-x.a.ranking!.ndcg_at_k!,0)/rankPairs.length:null},
 complete:stopped==="complete"&&entries.length>0&&entries.every(([,rows])=>rows.length===cases.length)};
}

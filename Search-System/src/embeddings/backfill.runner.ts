import {createHash} from "node:crypto";
import type {EmbeddingBackfillRepository} from "../repositories/embedding-backfill.repository.js";
import {EmbeddingError,type EmbeddingProfile,type EmbeddingResult} from "./embedding.types.js";
import {validateBackfillOptions,type BackfillOptions,type SourceDocument} from "./backfill.types.js";
export interface BackfillProvider {
 inspect(input:{texts:readonly string[];purpose:"document";signal?:AbortSignal}):Promise<{tokenCounts:number[];configurationHash:string;embeddingCalls:0}>;
 embed(input:{texts:readonly string[];purpose:"document";signal?:AbortSignal}):Promise<EmbeddingResult>;
}
const transient=new Set(["network_error","timeout","deadline_exceeded","retry_after_exceeds_budget"]);
export function retryDelay(error:unknown,attempt:number,maxAttempts:number):number|null {
 if(attempt>=maxAttempts || !(error instanceof EmbeddingError))return null;
 if(transient.has(error.code) || (error.code==="http_error" && error.httpStatus!==null && [408,429,500,502,503,504].includes(error.httpStatus)))
  return Math.min(3600,30*2**(attempt-1));
 return null;
}
export async function runEmbeddingBackfill(input:{repo:EmbeddingBackfillRepository;provider:BackfillProvider;profile:EmbeddingProfile;
 configurationHash:string;artifactSha256:string;options:BackfillOptions;write:boolean;signal?:AbortSignal}) {
 const {repo,provider,profile,options:o}=input;validateBackfillOptions(o);
 if(input.write && o.limit>1000)throw new Error("Write run limited to1000 documents; use repeated resumable runs");
 await repo.assertModel(o.modelId,profile,input.artifactSha256);
 const coverageBefore=await repo.coverage(o.kind,o.modelId);
 const start=performance.now(),controller=new AbortController();
 const cancel=()=>controller.abort();input.signal?.addEventListener("abort",cancel,{once:true});if(input.signal?.aborted)cancel();
 const timer=setTimeout(cancel,o.maxSeconds*1000);
 const report={mode:input.write?"write":"dry_run",kind:o.kind,model_id:o.modelId,configuration_hash:input.configurationHash,
  scanned:0,eligible:0,over_context:0,invalid_source:0,invalid_input:0,total_tokens:0,max_document_tokens:0,
  claimed:0,ready:0,failed:0,lost_lease:0,claim_skipped:0,embedding_requests:0,successful_provider_attempts:0,embedding_tokens:0,
  stopped:"source_exhausted",issues:[] as Array<{id:string;code:string;tokens?:number}>,issues_truncated:false,
  elapsed_ms:0,next_after:input.options.after??null as string|null,coverage_before:coverageBefore,coverage_after:{...coverageBefore}};
 const issue=(doc:SourceDocument,code:string,tokens?:number)=>{
  if(report.issues.length<200)report.issues.push({id:doc.id,code,...(tokens===undefined?{}:{tokens})});else report.issues_truncated=true;
 };
 let after:string|null=o.after??null,done=false;
 try {
  while(!done && report.scanned<o.limit) {
   if(controller.signal.aborted){report.stopped=input.signal?.aborted?"cancelled":"time_budget";break;}
   const docs=await repo.page(o.kind,after,Math.min(100,o.limit-report.scanned),input.write?o.modelId:null,o.maxAttempts);
   if(!docs.length)break;
   for(const doc of docs){
    if(controller.signal.aborted){report.stopped=input.signal?.aborted?"cancelled":"time_budget";done=true;break;}
    const previous=after;after=doc.id;report.scanned++;
    if(createHash("sha256").update(doc.text).digest("hex")!==doc.hash || !doc.template.trim()){
     report.invalid_source++;issue(doc,"source_hash_or_template_invalid");continue;
    }
    let tokens:number;
    try {
     const check=await provider.inspect({texts:[doc.text],purpose:"document",signal:controller.signal});
     if(check.configurationHash!==input.configurationHash || check.tokenCounts.length!==1)throw new Error("Profile changed");
     tokens=check.tokenCounts[0]!;
     if(!Number.isSafeInteger(tokens) || tokens<1)throw new Error("Invalid token count");
    }catch(e){
     if(e instanceof EmbeddingError && e.code==="invalid_input") {report.invalid_input++;issue(doc,"input_bytes_or_empty");continue;}
     if(controller.signal.aborted){report.stopped=input.signal?.aborted?"cancelled":"time_budget";done=true;break;}
     throw e; // Runtime/transport/config problems stop the run; never disguise as missing products.
    }
    report.max_document_tokens=Math.max(report.max_document_tokens,tokens);
    if(tokens>profile.contextTokens){report.over_context++;issue(doc,"input_too_long",tokens);continue;}
    if(report.total_tokens+tokens>o.maxTokens){after=previous;report.stopped="token_budget";done=true;break;}
    report.total_tokens+=tokens;report.eligible++;
    if(!input.write)continue;
    if(controller.signal.aborted){after=previous;report.stopped="time_budget";done=true;break;}
    const claim=await repo.claim(o.kind,doc,o.modelId,o.maxAttempts,o.leaseSeconds);
    if(!claim){report.claim_skipped++;continue;}
    report.claimed++;
    try {
     // Every HTTP request occurs AFTER the claim's autocommit completed. No DB transaction held across inference.
     report.embedding_requests++;
     const r=await provider.embed({texts:[doc.text],purpose:"document",signal:controller.signal});
     report.successful_provider_attempts+=r.attempts;report.embedding_tokens+=r.usage.promptTokens;
     if(r.configurationHash!==input.configurationHash || r.model!==profile.model || r.purpose!=="document" ||
      r.attempts!==1 || r.dimensions!==profile.dimensions || r.tokenCounts.length!==1 || r.tokenCounts[0]!==tokens || r.vectors.length!==1 ||
      r.vectors[0]!.length!==profile.dimensions || !r.vectors[0]!.every(n=>Number.isFinite(n) && Number.isFinite(Math.fround(n))) ||
      Math.abs(Math.hypot(...r.vectors[0]!)-1)>0.01)throw new EmbeddingError("invalid_response");
     if(await repo.finish(o.kind,claim,o.modelId,{vector:r.vectors[0]!}))report.ready++;else report.lost_lease++;
    }catch(e){
     // DB completion error propagates: uncertainty must not overwrite a successfully committed ready vector.
     if(!(e instanceof EmbeddingError))throw e;
     const code=e.code;issue(doc,code);
     const retry=code==="cancelled"?30:retryDelay(e,claim.attempt,o.maxAttempts);
     if(await repo.finish(o.kind,claim,o.modelId,{error:code,retrySeconds:retry}))report.failed++;else report.lost_lease++;
     if(["model_mismatch","invalid_configuration","invalid_response"].includes(code)) {report.stopped=code;done=true;break;}
    }
   }
  }
  if(!done && report.scanned>=o.limit)report.stopped="document_limit";
  report.next_after=after;report.coverage_after=await repo.coverage(o.kind,o.modelId);report.elapsed_ms=Math.round(performance.now()-start);
  return report;
 }finally{clearTimeout(timer);input.signal?.removeEventListener("abort",cancel);}
}

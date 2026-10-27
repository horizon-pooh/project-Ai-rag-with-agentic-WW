import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import {readEmbeddingConfiguration,embeddingConfigurationHash} from "../config/embedding.config.js";
import {createLlamaEmbeddingProvider} from "../embeddings/llama-embedding.provider.js";
import {EmbeddingError} from "../embeddings/embedding.types.js";

async function main():Promise<void> {
 const args=process.argv.slice(2);if(args.length>1 || (args[0]!==undefined && !["--dry-run","--live"].includes(args[0])))throw new EmbeddingError("invalid_input");
 const live=args[0]==="--live";
 let text:string;
 try{text=await readFile(".env.embedding.local","utf8");}
 catch(error){
  if(!live && typeof error==="object" && error!==null && "code" in error && error.code==="ENOENT"){
   console.log(JSON.stringify({status:"embedding_not_configured",mode:"dry_run",provider_calls:0,database_writes:0,
    message:"Adapter tests can pass before selecting the real model/profile. Configure the private profile before local inference."}));return;
  }
  throw new EmbeddingError("invalid_configuration");
 }
 const {profile,bounds,baseUrl,apiKey}=readEmbeddingConfiguration(parse(text));
 if(!live){console.log(JSON.stringify({status:"embedding_configuration_valid",mode:"dry_run",model:profile.model,dimensions:profile.dimensions,
   configuration_hash:embeddingConfigurationHash(profile),api_key_present:Boolean(apiKey),provider_calls:0,database_writes:0}));}
 else {
  // Explicit --live uses the local model only; no backfill or database writes.
  const provider=createLlamaEmbeddingProvider({profile,baseUrl,apiKey,bounds:{...bounds,maxAttempts:1}});
  const result=await provider.embed({purpose:"document",texts:["ทีวี OLED ขนาดหน้าจอ 55 นิ้ว สำหรับดูภาพยนตร์","เครื่องซักผ้าความจุซัก 9 กิโลกรัม สำหรับซักผ้า"]});
  const query=await provider.embed({purpose:"query",texts:["ทีวีสำหรับดูหนัง"]});
  console.log(JSON.stringify({status:"embedding_provider_ready",mode:"live",model:result.model,dimensions:result.dimensions,
   document_vectors:result.vectors.length,query_vectors:query.vectors.length,token_counts:result.tokenCounts,query_token_counts:query.tokenCounts,configuration_hash:result.configurationHash,attempts:result.attempts+query.attempts,usage:{documents:result.usage,query:query.usage},external_provider_calls:0,database_writes:0}));
 }
}
try {await main();} catch(error){
 console.error(JSON.stringify({status:"failed",code:error instanceof EmbeddingError?error.code:"embedding_check_failed",
  attempts:error instanceof EmbeddingError?error.attempts:0,
  message:"Check private embedding configuration/provider access; no key, text, raw response or vector is printed."}));process.exitCode=1;
}

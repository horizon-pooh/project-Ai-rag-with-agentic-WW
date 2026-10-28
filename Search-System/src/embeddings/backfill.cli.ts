import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import {Client} from "pg";
import {readEmbeddingConfiguration} from "../config/embedding.config.js";
/** Strict flags: no silently ignored typo can turn a dry-run into a write. */
export function backfillArguments(args:string[]) {
 const values:Record<string,string>={};let write=false;
 for(let i=0;i<args.length;i++){
  const key=args[i]!;
  if(key==="--write"){if(write)throw new Error("Duplicate flag");write=true;continue;}
  if(!["--kind","--model-id","--limit","--max-tokens","--max-seconds","--after"].includes(key) || key in values || !args[i+1] || args[i+1]!.startsWith("--"))throw new Error("Invalid CLI flag");
  values[key]=args[++i]!;
 }
 const number=(name:string,fallback:string)=>{const v=values[name]??fallback;if(!/^[1-9]\d*$/.test(v))throw new Error("Expected positive integer flag");return Number(v);};
 const kind=values["--kind"];if(kind!=="category" && kind!=="product")throw new Error("Specify category or product");
 return {write,options:{kind,modelId:values["--model-id"]??"",limit:number("--limit",write?"3":"20"),maxTokens:number("--max-tokens","100000"),
  maxSeconds:number("--max-seconds","120"),maxAttempts:3,leaseSeconds:120,after:values["--after"]??null}} as const;
}
export async function loadBackfillProfile() {
 const env=parse(await readFile(".env.embedding.local","utf8"));const config=readEmbeddingConfiguration(env);
 const sha=env.EMBEDDING_ARTIFACT_SHA256??"";
 if(!/^[0-9a-f]{64}$/.test(sha))throw new Error("Add verified file SHA256 locally before registration/backfill");
 return {...config,sha};
}
export async function ownerClient() {
 const env=parse(await readFile(".env.migration.local","utf8"));const u=new URL(env.MIGRATION_DATABASE_URL??"");
 if(!["postgresql:","postgres:"].includes(u.protocol) || !["127.0.0.1","localhost","[::1]"].includes(u.hostname) || u.username!=="antonius" ||
  u.pathname!=="/supabase_db_product" || u.password || u.search || u.hash || !env.MIGRATION_DATABASE_PASSWORD)throw new Error("Invalid private owner configuration");
 const client=new Client({host:u.hostname==="[::1]"?"::1":u.hostname,port:Number(u.port||5432),user:"antonius",database:"supabase_db_product",password:env.MIGRATION_DATABASE_PASSWORD,
  connectionTimeoutMillis:5000,statement_timeout:15000,lock_timeout:5000,application_name:"embedding-model-registration",options:"-c search_path=pg_catalog"});
 try {await client.connect();const row=(await client.query<{ok:boolean}>(`SELECT current_user='antonius' AND current_database()='supabase_db_product'
  AND (SELECT relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_class WHERE oid='public.embedding_models'::regclass) AS ok`)).rows[0];
  if(!row?.ok)throw new Error("Wrong registration owner");return client;
 }catch(e){await client.end().catch(()=>{});throw e;}
}

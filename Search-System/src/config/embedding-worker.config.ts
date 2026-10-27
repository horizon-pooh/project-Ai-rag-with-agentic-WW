import type {PoolConfig} from "pg";
export interface EmbeddingWorkerConfig {host:string;port:number;password:string}
export function readEmbeddingWorkerConfig(values:NodeJS.ProcessEnv):EmbeddingWorkerConfig {
 let url:URL;try{url=new URL(values.EMBEDDING_WORKER_DATABASE_URL??"");}catch{throw new Error("Invalid private worker database configuration");}
 if(!["postgres:","postgresql:"].includes(url.protocol) || !["127.0.0.1","localhost","[::1]"].includes(url.hostname) || url.username!=="embedding_worker" ||
  url.pathname!=="/supabase_db_product" || url.password || url.search || url.hash || !values.EMBEDDING_WORKER_DATABASE_PASSWORD?.trim())
  throw new Error("Invalid private worker database configuration");
 return {host:url.hostname==="[::1]"?"::1":url.hostname,port:Number(url.port||5432),password:values.EMBEDDING_WORKER_DATABASE_PASSWORD};
}
export function embeddingWorkerPoolOptions(config:EmbeddingWorkerConfig):PoolConfig {
 return {...config,user:"embedding_worker",database:"supabase_db_product",ssl:false,max:2,connectionTimeoutMillis:5000,idleTimeoutMillis:30000,
  statement_timeout:15000,query_timeout:20000,idle_in_transaction_session_timeout:15000,lock_timeout:5000,
  application_name:"catalog-embedding-worker",options:"-c default_transaction_read_only=off -c search_path=pg_catalog,public,extensions"};
}

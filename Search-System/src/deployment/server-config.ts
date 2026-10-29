import {lstat,readFile} from "node:fs/promises";
import {isIP} from "node:net";
import {readDatabaseConfig} from "../config/database.config.js";
import {readToolApiConfig} from "../config/tool-api.config.js";
import {readProductHybridConfig} from "../config/product-hybrid.config.js";
import {readCategorySemanticConfig} from "../config/category-semantic.config.js";
import {readQueryCacheConfig} from "../config/query-cache.config.js";
export const SERVER_KEYS=["NODE_ENV","HOST","PORT","DATA_SOURCE","SEARCH_DATABASE_URL","SEARCH_DATABASE_PASSWORD","SEARCH_DATABASE_POOL_MAX","SEARCH_API_ENABLED","SEARCH_API_TOKEN",
 "SEARCH_PRODUCT_HYBRID_ENABLED","SEARCH_PRODUCT_HYBRID_MODEL_ID","SEARCH_CATEGORY_SEMANTIC_ENABLED","SEARCH_CATEGORY_SEMANTIC_MODEL_ID","SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT","SEARCH_CATEGORY_SEMANTIC_MIN_SCORE","SEARCH_CATEGORY_SEMANTIC_MIN_GAP","SEARCH_CATEGORY_SEMANTIC_CALIBRATION_ID",
 "SEARCH_QUERY_CACHE_ENABLED","SEARCH_QUERY_CACHE_MAX_ENTRIES","SEARCH_QUERY_CACHE_MAX_BYTES","SEARCH_QUERY_CACHE_TTL_MS","SEARCH_QUERY_CACHE_MAX_IN_FLIGHT",
 "EMBEDDING_PROVIDER","EMBEDDING_BASE_URL","EMBEDDING_MODEL","EMBEDDING_ARTIFACT","EMBEDDING_RUNTIME_VERSION","EMBEDDING_DIMENSIONS","EMBEDDING_CONTEXT_TOKENS","EMBEDDING_DISTANCE_METRIC","EMBEDDING_POOLING","EMBEDDING_NORMALIZATION","EMBEDDING_CONFIG_VERSION","EMBEDDING_QUERY_PREFIX_JSON","EMBEDDING_DOCUMENT_PREFIX_JSON","EMBEDDING_ARTIFACT_SHA256","EMBEDDING_MAX_ATTEMPTS","EMBEDDING_ATTEMPT_TIMEOUT_MS","EMBEDDING_TOTAL_TIMEOUT_MS","LLAMA_EMBEDDING_API_KEY"] as const;
export function isPrivateHost(host:string):boolean {
 if(["127.0.0.1","localhost","[::1]"].includes(host))return true;
 if(isIP(host)!==4)return false;const n=host.split(".").map(Number);return n[0]===100&&n[1]!>=64&&n[1]!<=127;
}
export function validateServerEnvironment(raw:unknown):Record<string,string> {
 if(!raw||typeof raw!=="object"||Array.isArray(raw))throw new Error("Invalid server configuration");
 const entries=Object.entries(raw);if(entries.some(([k,v])=>!SERVER_KEYS.includes(k as typeof SERVER_KEYS[number])||typeof v!=="string"||v.length>8192||v.includes("\0")))throw new Error("Invalid server configuration");
 const e=Object.fromEntries(entries) as Record<string,string>;
 if(e.NODE_ENV!=="production"||!isPrivateHost(e.HOST??"")||!/^\d+$/.test(e.PORT??"")||Number(e.PORT)<1024||Number(e.PORT)>65535||e.DATA_SOURCE!=="postgres")throw new Error("Invalid server binding");
 const db=readDatabaseConfig(e),api=readToolApiConfig(e),hybrid=readProductHybridConfig(e),category=readCategorySemanticConfig(e);readQueryCacheConfig(e);
 if(!db||db.host!=="127.0.0.1"||db.port!==5432||!api||!hybrid||!category||hybrid.baseUrl!=="http://127.0.0.1:8081/v1"||category.baseUrl!==hybrid.baseUrl||hybrid.configurationHash!==category.configurationHash||hybrid.modelId!==category.modelId||hybrid.artifactSha256!==category.artifactSha256)throw new Error("Invalid server endpoints");
 return {...e};
}
export function createServerEnvironment(source:NodeJS.ProcessEnv,host="100.116.20.59") {
 const chosen=Object.fromEntries(SERVER_KEYS.flatMap(k=>source[k]===undefined?[]:[[k,source[k]!]]));
 return validateServerEnvironment({...chosen,NODE_ENV:"production",HOST:host,PORT:"3000",DATA_SOURCE:"postgres",SEARCH_DATABASE_URL:"postgresql://search_api@127.0.0.1:5432/supabase_db_product",SEARCH_API_ENABLED:"true",SEARCH_DATABASE_POOL_MAX:"5",EMBEDDING_BASE_URL:"http://127.0.0.1:8081/v1",SEARCH_PRODUCT_HYBRID_ENABLED:"true",SEARCH_PRODUCT_HYBRID_MODEL_ID:source.SEARCH_PRODUCT_HYBRID_MODEL_ID??"EM0001",SEARCH_CATEGORY_SEMANTIC_ENABLED:"true",SEARCH_CATEGORY_SEMANTIC_MODEL_ID:source.SEARCH_CATEGORY_SEMANTIC_MODEL_ID??"EM0001",SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT:"false"});
}
export async function loadServerEnvironment(path:string) {
 const info=await lstat(path);if(!info.isFile()||info.size>65536||(info.mode&0o077)!==0||(process.getuid&&info.uid!==process.getuid()))throw new Error("Require own private configuration file");
 return validateServerEnvironment(JSON.parse(await readFile(path,"utf8")));
}
export function applyServerEnvironment(values:Record<string,string>,target:NodeJS.ProcessEnv=process.env) {
 for(const key of Object.keys(target))if(key.startsWith("SEARCH_")||key.startsWith("EMBEDDING_")||key==="LLAMA_EMBEDDING_API_KEY")delete target[key];
 Object.assign(target,values);
}

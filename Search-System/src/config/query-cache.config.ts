export interface QueryCacheConfig {
 enabled:boolean;maxEntries:number;maxBytes:number;ttlMs:number;maxInFlight:number;maxWaiters:number;timeoutMs:number;
}
export const DEFAULT_QUERY_CACHE_CONFIG:QueryCacheConfig={enabled:true,maxEntries:128,maxBytes:4*1024*1024,ttlMs:60000,maxInFlight:4,maxWaiters:64,timeoutMs:5000};
export function validateQueryCacheConfig(c:QueryCacheConfig):void {
 if(typeof c.enabled!=="boolean")throw new Error("Invalid query cache switch");
 for(const [v,min,max] of [[c.maxEntries,1,1024],[c.maxBytes,1024,16*1024*1024],[c.ttlMs,1,300000],[c.maxInFlight,1,16],[c.maxWaiters,1,128],[c.timeoutMs,1,5000]])
  if(!Number.isSafeInteger(v) || v<min || v>max)throw new Error("Invalid query cache bound");
}
export function readQueryCacheConfig(env:NodeJS.ProcessEnv):QueryCacheConfig {
 const enabled=env.SEARCH_QUERY_CACHE_ENABLED??"true";if(!["true","false"].includes(enabled))throw new Error("Invalid query cache switch");
 const number=(key:string,fallback:number)=>{const value=env[key];if(value===undefined)return fallback;if(!/^[1-9]\d*$/.test(value))throw new Error("Invalid query cache value");return Number(value);};
 const c={...DEFAULT_QUERY_CACHE_CONFIG,enabled:enabled==="true",maxEntries:number("SEARCH_QUERY_CACHE_MAX_ENTRIES",128),maxBytes:number("SEARCH_QUERY_CACHE_MAX_BYTES",4*1024*1024),
  ttlMs:number("SEARCH_QUERY_CACHE_TTL_MS",60000),maxInFlight:number("SEARCH_QUERY_CACHE_MAX_IN_FLIGHT",4)};
 validateQueryCacheConfig(c);return c;
}

import {createHash} from "node:crypto";
import {EmbeddingError,type EmbeddingProfile,type EmbeddingBounds} from "../embeddings/embedding.types.js";
export const DEFAULT_EMBEDDING_BOUNDS:EmbeddingBounds={maxBatchSize:16,maxInputBytes:16000,maxBatchBytes:128000,
 maxResponseBytes:4*1024*1024,maxAttempts:3,attemptTimeoutMs:10000,totalTimeoutMs:30000,baseDelayMs:250,maxDelayMs:5000};
const invalid=():never=>{throw new EmbeddingError("invalid_configuration");};
export function validateEmbeddingProfile(p:EmbeddingProfile):void {
 if(!p || p.provider!=="llama_cpp" || typeof p.model!=="string" || !p.model.trim() || p.model.length>500 || /[\x00-\x1f]/.test(p.model) ||
   !Number.isInteger(p.dimensions) || p.dimensions<1 || p.dimensions>65536 || p.distanceMetric!=="cosine" ||
   typeof p.version!=="string" || !/^[a-zA-Z0-9_.-]{1,80}$/.test(p.version) || p.pooling!=="last" || p.normalization!=="server_l2" || !Number.isInteger(p.contextTokens) || p.contextTokens<1 || p.contextTokens>32768 ||
   typeof p.runtimeVersion!=="string" || !/^[a-zA-Z0-9_.-]{1,160}$/.test(p.runtimeVersion) || typeof p.artifact!=="string" || !p.artifact.trim() || p.artifact.length>200)invalid();
 for(const item of [p.query,p.document])if(!item || typeof item.prefix!=="string" || Buffer.byteLength(item.prefix)>4096)invalid();
}
export function validateEmbeddingBounds(b:EmbeddingBounds):void {
 const limits:Record<keyof EmbeddingBounds,[number,number]>={maxBatchSize:[1,64],maxInputBytes:[1,128000],maxBatchBytes:[1,1024000],
  maxResponseBytes:[1024,16*1024*1024],maxAttempts:[1,5],attemptTimeoutMs:[10,60000],totalTimeoutMs:[10,60000],baseDelayMs:[0,5000],maxDelayMs:[0,10000]};
 for(const [key,[min,max]] of Object.entries(limits) as Array<[keyof EmbeddingBounds,[number,number]]>)
  if(!Number.isInteger(b[key]) || b[key]<min || b[key]>max)invalid();
 if(b.attemptTimeoutMs>b.totalTimeoutMs || b.baseDelayMs>b.maxDelayMs || b.maxInputBytes>b.maxBatchBytes)invalid();
}
/** Explicit field order; semantic configuration excludes API keys and transport retry limits. */
export function embeddingConfigurationHash(profile:EmbeddingProfile):string {
 validateEmbeddingProfile(profile);
 return createHash("sha256").update(JSON.stringify({provider:profile.provider,model:profile.model,dimensions:profile.dimensions,
  distanceMetric:profile.distanceMetric,version:profile.version,pooling:profile.pooling,normalization:profile.normalization,contextTokens:profile.contextTokens,
  runtimeVersion:profile.runtimeVersion,artifact:profile.artifact,query:{prefix:profile.query.prefix},document:{prefix:profile.document.prefix}})).digest("hex");
}
export function readEmbeddingConfiguration(values:NodeJS.ProcessEnv):{profile:EmbeddingProfile;bounds:EmbeddingBounds;baseUrl:string;apiKey:string|undefined} {
 const int=(key:string,fallback?:number):number=>{
  const v=values[key];if(v===undefined && fallback!==undefined)return fallback;
  if(!v || !/^\d+$/.test(v))return invalid();return Number(v);
 };
 const prefix=(key:string):string=>{
  try {const v=JSON.parse(values[key]??'""') as unknown;if(typeof v!=="string")return invalid();return v;}catch{return invalid();}
 };
 const profile:EmbeddingProfile={provider:"llama_cpp",model:values.EMBEDDING_MODEL??"",dimensions:int("EMBEDDING_DIMENSIONS"),distanceMetric:"cosine",
  version:values.EMBEDDING_CONFIG_VERSION??"",pooling:"last",normalization:"server_l2",contextTokens:int("EMBEDDING_CONTEXT_TOKENS"),
  runtimeVersion:values.EMBEDDING_RUNTIME_VERSION??"",artifact:values.EMBEDDING_ARTIFACT??"",
  query:{prefix:prefix("EMBEDDING_QUERY_PREFIX_JSON")},
  document:{prefix:prefix("EMBEDDING_DOCUMENT_PREFIX_JSON")}};
 if(values.EMBEDDING_PROVIDER!=="llama_cpp" || values.EMBEDDING_DISTANCE_METRIC!=="cosine")invalid();
 const bounds={...DEFAULT_EMBEDDING_BOUNDS,maxAttempts:int("EMBEDDING_MAX_ATTEMPTS",3),attemptTimeoutMs:int("EMBEDDING_ATTEMPT_TIMEOUT_MS",10000),
  totalTimeoutMs:int("EMBEDDING_TOTAL_TIMEOUT_MS",30000)};
 validateEmbeddingProfile(profile);validateEmbeddingBounds(bounds);
 if(values.EMBEDDING_POOLING!=="last" || values.EMBEDDING_NORMALIZATION!=="server_l2")invalid();
 const baseUrl=validateLocalEmbeddingUrl(values.EMBEDDING_BASE_URL??"");
 const apiKey=values.LLAMA_EMBEDDING_API_KEY || undefined;
 if(apiKey!==undefined && (!/^[\x21-\x7e]{1,512}$/.test(apiKey)))invalid();
 return {profile,bounds,baseUrl,apiKey};
}

/** Model is loopback on the server or reached through an SSH forward; never caller-supplied arbitrary hosts. */
export function validateLocalEmbeddingUrl(value:string):string {
 let url:URL;try{url=new URL(value);}catch{return invalid();}
 if(url.protocol!=="http:" || !["127.0.0.1","localhost","[::1]"].includes(url.hostname) ||
  !["/v1","/v1/"].includes(url.pathname) || url.username || url.password || url.search || url.hash)invalid();
 return url.origin+"/v1";
}

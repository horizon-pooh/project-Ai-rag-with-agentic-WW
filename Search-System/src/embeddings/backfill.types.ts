import type {EmbeddingProfile} from "./embedding.types.js";
export type DocumentKind = "category" | "product";
export interface SourceDocument { id:string; text:string; hash:string; template:string }
export interface ClaimedDocument extends SourceDocument { lease:string; attempt:number }
export interface BackfillOptions {
 kind:DocumentKind; modelId:string; limit:number; maxTokens:number; maxSeconds:number;
 maxAttempts:number; leaseSeconds:number; after?:string|null;
}
export function validateBackfillOptions(o:BackfillOptions):void {
 if(!["category","product"].includes(o.kind) || !/^EM\d{4}$/.test(o.modelId))throw new Error("Invalid backfill selector");
 if(o.after!=null && !(o.kind==="product"?/^1\d{5}$/:/^[A-Z]{2}\d{4}$/).test(o.after))throw new Error("Invalid cursor");
 for(const [v,min,max] of [[o.limit,1,20000],[o.maxTokens,1,100000000],[o.maxSeconds,1,3600],
  [o.maxAttempts,1,5],[o.leaseSeconds,90,600]])if(!Number.isSafeInteger(v) || v<min || v>max)throw new Error("Invalid backfill bound");
}
export function verifiedModelSettings(profile:EmbeddingProfile,configurationHash:string,artifactSha256:string) {
 if(!/^[0-9a-f]{64}$/.test(configurationHash) || !/^[0-9a-f]{64}$/.test(artifactSha256))throw new Error("Require lowercase SHA256 digests");
 return {configuration_hash:configurationHash,artifact_sha256:artifactSha256,profile:structuredClone(profile)};
}

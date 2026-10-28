import {readEmbeddingConfiguration,embeddingConfigurationHash} from "./embedding.config.js";
export interface CategorySemanticPolicy {autoSelect:boolean;minScore:number|null;minGap:number|null;calibrationId:string|null}
export function readCategorySemanticConfig(env:NodeJS.ProcessEnv) {
 const enabled=env.SEARCH_CATEGORY_SEMANTIC_ENABLED;
 if(enabled===undefined || enabled==="false")return null;
 if(enabled!=="true")throw new Error("Invalid category semantic switch");
 const modelId=env.SEARCH_CATEGORY_SEMANTIC_MODEL_ID??"",sha=env.EMBEDDING_ARTIFACT_SHA256??"";
 if(!/^EM\d{4}$/.test(modelId) || !/^[0-9a-f]{64}$/.test(sha))throw new Error("Require explicit verified semantic model");
 const auto=env.SEARCH_CATEGORY_SEMANTIC_AUTO_SELECT??"false";if(!["true","false"].includes(auto))throw new Error("Invalid selection switch");
 const score=(key:string,min:number,max:number)=>{const s=env[key];if(!s || !/^(?:0(?:\.\d+)?|1(?:\.0+)?)$/.test(s))throw new Error("Require measured category threshold");
  const n=Number(s);if(n<min || n>max)throw new Error("Invalid category threshold");return n;};
 const policy:CategorySemanticPolicy={autoSelect:auto==="true",minScore:null,minGap:null,calibrationId:null};
 if(policy.autoSelect){policy.minScore=score("SEARCH_CATEGORY_SEMANTIC_MIN_SCORE",0,1);policy.minGap=score("SEARCH_CATEGORY_SEMANTIC_MIN_GAP",0.000001,1);
  policy.calibrationId=env.SEARCH_CATEGORY_SEMANTIC_CALIBRATION_ID??null;
  if(!policy.calibrationId || !/^[a-zA-Z0-9_.-]{1,80}$/.test(policy.calibrationId))throw new Error("Require calibration record ID");}
 const embedding=readEmbeddingConfiguration(env);
 return {...embedding,modelId,artifactSha256:sha,configurationHash:embeddingConfigurationHash(embedding.profile),policy};
}
export type CategorySemanticConfig=NonNullable<ReturnType<typeof readCategorySemanticConfig>>;

import {readEmbeddingConfiguration,embeddingConfigurationHash} from "./embedding.config.js";
export function readProductHybridConfig(env:NodeJS.ProcessEnv) {
 const enabled=env.SEARCH_PRODUCT_HYBRID_ENABLED;
 if(enabled===undefined || enabled==="false")return null;
 if(enabled!=="true")throw new Error("Invalid hybrid switch");
 const modelId=env.SEARCH_PRODUCT_HYBRID_MODEL_ID??"",artifactSha256=env.EMBEDDING_ARTIFACT_SHA256??"";
 if(!/^EM\d{4}$/.test(modelId) || !/^[0-9a-f]{64}$/.test(artifactSha256))throw new Error("Require verified hybrid model");
 const embedding=readEmbeddingConfiguration(env);
 return {...embedding,modelId,artifactSha256,configurationHash:embeddingConfigurationHash(embedding.profile)};
}
export type ProductHybridConfig=NonNullable<ReturnType<typeof readProductHybridConfig>>;

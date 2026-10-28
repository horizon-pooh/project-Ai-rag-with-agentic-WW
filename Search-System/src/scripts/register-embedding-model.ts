import {embeddingConfigurationHash} from "../config/embedding.config.js";
import {createLlamaEmbeddingProvider} from "../embeddings/llama-embedding.provider.js";
import {loadBackfillProfile,ownerClient} from "../embeddings/backfill.cli.js";
import {registerEmbeddingModel} from "../repositories/embedding-backfill.repository.js";
import {databaseErrorCode} from "../database/postgres.js";
import type {Client} from "pg";
let client:Client|undefined;
try {
 const args=process.argv.slice(2);
 if(args.length!==3 || args[0]!=="--model-id" || !/^EM\d{4}$/.test(args[1]??"") || args[2]!=="--activate")throw new Error("Require --model-id EMnnnn --activate");
 const c=await loadBackfillProfile();
 // Runtime model/build/context verification and tokenization only, no embedding generation.
 await createLlamaEmbeddingProvider(c).inspect({texts:["embedding readiness"],purpose:"document"});
 client=await ownerClient();await client.query("BEGIN");
 await registerEmbeddingModel(client,args[1]!,c.profile,c.sha);await client.query("COMMIT");
 console.log(JSON.stringify({status:"embedding_model_registered",model_id:args[1],configuration_hash:embeddingConfigurationHash(c.profile),
  dimensions:c.profile.dimensions,embedding_calls:0,source_writes:0,other_models_modified:0}));
}catch(e){if(client)await client.query("ROLLBACK").catch(()=>{});console.error(JSON.stringify({status:"failed",code:databaseErrorCode(e),
 message:"Check model ID, locally verified artifact SHA256/profile/runtime and owner configuration. No credentials or raw error printed."}));process.exitCode=1;}
finally{await client?.end();}

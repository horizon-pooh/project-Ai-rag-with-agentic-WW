import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import {backfillArguments,loadBackfillProfile} from "../embeddings/backfill.cli.js";
import {embeddingConfigurationHash} from "../config/embedding.config.js";
import {readEmbeddingWorkerConfig} from "../config/embedding-worker.config.js";
import {createEmbeddingWorkerDatabase} from "../database/embedding-worker.js";
import {EmbeddingBackfillRepository} from "../repositories/embedding-backfill.repository.js";
import {createLlamaEmbeddingProvider} from "../embeddings/llama-embedding.provider.js";
import {runEmbeddingBackfill} from "../embeddings/backfill.runner.js";
import {EmbeddingError} from "../embeddings/embedding.types.js";
import {databaseErrorCode} from "../database/postgres.js";
let database:ReturnType<typeof createEmbeddingWorkerDatabase>|undefined;
const controller=new AbortController(),cancel=()=>controller.abort();
process.once("SIGINT",cancel);process.once("SIGTERM",cancel);
try {
 const args=backfillArguments(process.argv.slice(2)),c=await loadBackfillProfile();
 database=createEmbeddingWorkerDatabase(readEmbeddingWorkerConfig(parse(await readFile(".env.embedding-worker.local","utf8"))));
 await database.check();
 const report=await runEmbeddingBackfill({repo:new EmbeddingBackfillRepository(database.pool),provider:createLlamaEmbeddingProvider({...c,bounds:{...c.bounds,maxAttempts:1}}),profile:c.profile,
  configurationHash:embeddingConfigurationHash(c.profile),artifactSha256:c.sha,...args,signal:controller.signal});
 console.log(JSON.stringify({status:"embedding_backfill_report",...report},null,2));
 if(["model_mismatch","invalid_configuration","invalid_response","cancelled"].includes(report.stopped))process.exitCode=1;
}catch(e){console.error(JSON.stringify({status:"failed",code:e instanceof EmbeddingError?e.code:databaseErrorCode(e),
 message:"Backfill stopped. Check private settings, active model and runtime; use coverage to inspect partial progress. No text, vectors, credentials or raw database error printed."}));process.exitCode=1;}
finally{await database?.close();process.removeListener("SIGINT",cancel);process.removeListener("SIGTERM",cancel);}

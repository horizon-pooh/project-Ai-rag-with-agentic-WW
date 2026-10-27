import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import assert from "node:assert/strict";
import {readEmbeddingWorkerConfig} from "../config/embedding-worker.config.js";
import {createEmbeddingWorkerDatabase} from "../database/embedding-worker.js";
import {inspectEmbeddingStorage} from "../repositories/embedding-storage.repository.js";
import {databaseErrorCode} from "../database/postgres.js";

try {
 const values=parse(await readFile(".env.embedding-worker.local","utf8"));
 const database=createEmbeddingWorkerDatabase(readEmbeddingWorkerConfig(values));
 try {
  const identity=await database.check();const client=await database.pool.connect();
  try {
   await client.query("BEGIN READ ONLY");
   const audit=await inspectEmbeddingStorage(client);assert.ok(audit.rls_ready && audit.leases && audit.guards_ready);
   const drafts=(await client.query<{count:number}>("SELECT count(*)::integer AS count FROM public.products WHERE status='draft'")).rows[0]!.count;assert.equal(drafts,0);
   console.log(JSON.stringify({status:"embedding_worker_ready",identity,visible_counts:audit.counts,visible_drafts:drafts,
    models:audit.models.length,writes_performed:0,backfill_started:false},null,2));
  }finally{try{await client.query("ROLLBACK");}finally{client.release();}}
 }finally{await database.close();}
} catch(error){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),message:"Check worker role/login, private config, tunnel and migration0007. Do not paste passwords."}));process.exitCode=1;}

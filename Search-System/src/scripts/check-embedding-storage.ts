import {readFile} from "node:fs/promises";
import {parse} from "dotenv";
import {Client} from "pg";
import assert from "node:assert/strict";
import {inspectEmbeddingStorage} from "../repositories/embedding-storage.repository.js";
import {readEmbeddingConfiguration} from "../config/embedding.config.js";
import {databaseErrorCode} from "../database/postgres.js";

let client:Client|undefined;
try {
 const values=parse(await readFile(".env.migration.local","utf8"));const url=new URL(values.MIGRATION_DATABASE_URL??"");
 if(!["postgresql:","postgres:"].includes(url.protocol) || !["127.0.0.1","localhost","[::1]"].includes(url.hostname) || url.username!=="antonius" ||
  url.pathname!=="/supabase_db_product" || url.password || url.search || url.hash || !values.MIGRATION_DATABASE_PASSWORD)throw new Error("Invalid owner configuration");
 const profile=readEmbeddingConfiguration(parse(await readFile(".env.embedding.local","utf8"))).profile;
 client=new Client({host:url.hostname==="[::1]"?"::1":url.hostname,port:Number(url.port||5432),user:"antonius",database:"supabase_db_product",password:values.MIGRATION_DATABASE_PASSWORD,
  connectionTimeoutMillis:5000,statement_timeout:15000,application_name:"embedding-storage-audit",options:"-c search_path=pg_catalog"});
 await client.connect();await client.query("BEGIN READ ONLY");
 const identity=(await client.query<{database:string;user:string;owns:boolean}>(`SELECT current_database() AS database,current_user AS user,
   (SELECT relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_class WHERE oid='public.embedding_models'::regclass) AS owns`)).rows[0]!;
 assert.equal(identity.database,"supabase_db_product");assert.equal(identity.user,"antonius");assert.equal(identity.owns,true);
 const audit=await inspectEmbeddingStorage(client,profile);
 assert.ok(audit.extension.some(e=>e.schema==="extensions"));assert.ok(audit.rls_ready && audit.leases && audit.dimension_unlocked && audit.guards_ready && audit.model_guard_ready);
 console.log(JSON.stringify({status:"embedding_storage_ready",model_registration_required:audit.matching_active_models.length!==1,...audit},null,2));
} catch(error){console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),message:"Check private owner/profile configuration, baseline and migration0007. No credentials or raw database error printed."}));process.exitCode=1;}
finally{if(client){try{await client.query("ROLLBACK");}catch{}await client.end();}}

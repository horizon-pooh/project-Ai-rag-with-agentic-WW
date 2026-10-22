import { config as loadEnv } from "dotenv";
import { Client } from "pg";
import { resolve } from "node:path";
import { readFile } from "node:fs/promises";
import { applyMigrations, inspectMigrations } from "../database/migrations.js";
import { databaseErrorCode } from "../database/postgres.js";

const action = process.argv[2];
if (!["plan","up","down"].includes(action ?? "")) throw new Error("Use db:migrate:plan, db:migrate:up or db:migrate:down");
// Do not load admin secrets into the API's regular .env or use global process.env as a credential source.
const variables: NodeJS.ProcessEnv = {};
loadEnv({ path: ".env.migration.local", processEnv: variables, quiet: true });
let client: Client | undefined;
try {
  const url = new URL(variables.MIGRATION_DATABASE_URL ?? "");
  if (!["postgresql:","postgres:"].includes(url.protocol) || !["127.0.0.1","localhost","[::1]"].includes(url.hostname) ||
      url.username !== "antonius" || url.pathname !== "/supabase_db_product" || url.password || url.search || url.hash ||
      !variables.MIGRATION_DATABASE_PASSWORD) throw new Error("Invalid migration configuration");
  client = new Client({ host: url.hostname === "[::1]" ? "::1" : url.hostname,
    port: Number(url.port || "5432"), user: "antonius", database: "supabase_db_product",
    password: variables.MIGRATION_DATABASE_PASSWORD, ssl: false,
    connectionTimeoutMillis: 5000, statement_timeout: 30000, lock_timeout: 5000,
    application_name: "search-schema-migration", options: "-c search_path=pg_catalog,public,extensions" });
  await client.connect();
  const identity = (await client.query<{ database: string; user: string; owns_catalog: boolean }>(`
    SELECT current_database() AS database,current_user AS user,
      EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relname='products' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS owns_catalog
  `)).rows[0];
  if (identity.database !== "supabase_db_product" || identity.user !== "antonius" || !identity.owns_catalog) {
    throw new Error("Migration requires the verified catalog owner");
  }
  const directory = resolve("migrations");
  if (action === "plan") {
    await client.query("BEGIN READ ONLY");
    try { console.log(JSON.stringify(await inspectMigrations(client,directory),null,2)); }
    finally { await client.query("ROLLBACK"); }
  } else {
    // PostgreSQL/role config may default to read-only; fail clearly rather than silently overriding it.
    const mode = await client.query<{ mode: string }>("SELECT current_setting('default_transaction_read_only') AS mode");
    if (mode.rows[0].mode !== "off") throw new Error("Migration owner must use a read-write session");
    await readFile(resolve("docs/database-baseline.md"));
    console.log(JSON.stringify({ direction: action, applied: await applyMigrations(client,directory,action as "up"|"down") },null,2));
  }
} catch (error) {
  console.error(JSON.stringify({ status: "failed",code: databaseErrorCode(error),
    message: "Migration stopped. Check private migration configuration, owner identity, schema baseline and pinned checksums; do not retry with the API account." }));
  process.exitCode = 1;
} finally { await client?.end(); }

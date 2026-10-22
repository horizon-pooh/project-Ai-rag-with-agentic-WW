import "dotenv/config";
import { readDatabaseConfig } from "../config/database.config.js";
import { createSearchDatabase, databaseErrorCode } from "../database/postgres.js";
import type { PoolClient } from "pg";

function ensure(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
async function expectDenied(client: PoolClient, statement: string) {
  await client.query("SAVEPOINT access_probe");
  let denied = false;
  try { await client.query(statement); }
  catch (error) { denied = databaseErrorCode(error) === "42501"; }
  finally { await client.query("ROLLBACK TO SAVEPOINT access_probe"); await client.query("RELEASE SAVEPOINT access_probe"); }
  ensure(denied,"Expected insufficient_privilege from restricted operation");
}

async function main() {
  const config = readDatabaseConfig(process.env);
  ensure(config,"Set DATA_SOURCE=postgres");
  const database = createSearchDatabase(config);
  try {
    await database.check();
    const client = await database.pool.connect();
    try {
      await client.query("BEGIN READ ONLY");
      const summary = (await client.query<{ visible: number; drafts: number; categories: number; published_sections: number; draft_sections: number }>(`
        SELECT (SELECT count(*)::int FROM public.products) AS visible,
          (SELECT count(*)::int FROM public.products WHERE status<>'published') AS drafts,
          (SELECT count(*)::int FROM public.category_search_catalog) AS categories,
          (SELECT count(*)::int FROM public.product_content_sections WHERE product_id=100000) AS published_sections,
          (SELECT count(*)::int FROM public.product_content_sections WHERE product_id=106045) AS draft_sections
      `)).rows[0];
      ensure(summary.visible>0 && summary.drafts===0,"Published scope failed");
      ensure(summary.categories>0 && summary.published_sections===13 && summary.draft_sections===0,"Content/metadata visibility failed");
      for (const table of ["product_attribute_values","product_features","product_search_documents","product_content_overrides"]) {
        const rows = await client.query(`SELECT 1 FROM public.${table} WHERE product_id=$1 LIMIT 1`,[106045]);
        ensure(rows.rows.length===0,"Draft child data leaked");
      }
      const product = (await client.query<{ model: string }>("SELECT model FROM public.products WHERE id=100000")).rows[0];
      ensure(product,"Baseline published Article missing");
      const search = await client.query<{ id: number }>("SELECT id FROM public.search_products(p_query=>$1,p_limit=>5)",[product.model]);
      ensure(search.rows.some(row => row.id===100000),"Existing search function unavailable to reader");
      for (const statement of [
        "EXPLAIN SELECT * FROM dataset_admin.import_runs",
        "EXPLAIN SELECT * FROM dataset_admin.brand_category_evidence",
        "EXPLAIN SELECT * FROM public.category_embeddings",
        "EXPLAIN SELECT * FROM public.product_embeddings",
        "EXPLAIN SELECT * FROM public.embedding_models",
        "EXPLAIN SELECT * FROM public.category_search_documents",
        // EXPLAIN without ANALYZE checks permissions but never executes DML.
        "EXPLAIN UPDATE public.products SET name_th=name_th WHERE false",
        "EXPLAIN DELETE FROM public.products WHERE false",
      ]) await expectDenied(client,statement);
      const write = (await client.query<{ allowed: boolean }>(`
        SELECT coalesce(bool_or(has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')),false) AS allowed
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('public','dataset_admin') AND c.relkind IN ('r','p')
      `)).rows[0];
      ensure(!write.allowed,"Unexpected write privilege");
      console.log(JSON.stringify({ status:"passed",visible_products:summary.visible,visible_drafts:summary.drafts,
        categories:summary.categories,published_sections:summary.published_sections,draft_sections:summary.draft_sections,
        existing_search:true,private_tables_denied:true,write_denied:true },null,2));
    } finally { await client.query("ROLLBACK"); client.release(); }
  } finally { await database.close(); }
}
try { await main(); }
catch (error) {
  console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
    message:"Reader access checks failed; review migration, RLS and baseline Articles before committing."}));
  process.exitCode=1;
}
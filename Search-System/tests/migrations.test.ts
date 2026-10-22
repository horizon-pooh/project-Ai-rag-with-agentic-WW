import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, readFile, writeFile, copyFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import type { ClientBase } from "pg";
import { applyMigrations, inspectMigrations } from "../src/database/migrations.js";

test("PostgreSQL 18 isolated fixture: migration runner, source checksums, RLS and rollback", async t => {
  const db = new PGlite();
  const directory = await mkdtemp(join(tmpdir(),"search-reader-migrations-"));
  const name = "0001_search_reader_access.sql";
  const source = await readFile(resolve("migrations",name),"utf8");
  await copyFile(resolve("migrations",name),join(directory,name));
  // Protocol adapter is test-only. Runtime uses a real node-postgres ClientBase.
  const client = { async query(query: string | {text: string;values?:unknown[]},values?:unknown[]) {
    const text = typeof query === "string" ? query : query.text;
    const parameters = values ?? (typeof query === "object" ? query.values : undefined);
    const result = parameters?.length ? await db.query(text,parameters) : (await db.exec(text)).at(-1);
    return { rows: result?.rows ?? [], rowCount: result?.affectedRows ?? 0 };
  }} as unknown as ClientBase;
  const number = async (sql: string) => Number((await db.query<{n:number}>(sql)).rows[0].n);
  const reader = async (action: () => Promise<void>) => {
    await db.exec("SET ROLE search_api");
    try { await action(); } finally { await db.exec("RESET ROLE"); }
  };
  try {
    await db.exec(await readFile(resolve("tests/fixtures/search-access.sql"),"utf8"));
    await t.test("plan is read-only and starts with one pending migration", async () => {
      const plan = await inspectMigrations(client,directory);
      assert.deepEqual(plan.pending,["0001_search_reader_access"]);
      assert.equal((await db.query<{n:string|null}>("SELECT to_regnamespace('search_migrations')::text n")).rows[0].n,null);
    });
    await t.test("migration runs through the real node-pg-migrate runner", async () => {
      assert.deepEqual(await applyMigrations(client,directory,"up"),["0001_search_reader_access"]);
    });
    await t.test("rerunning up is a no-op, not duplicate policy creation", async () => {
      assert.deepEqual(await applyMigrations(client,directory,"up"),[]);
      assert.equal(await number("SELECT count(*) n FROM search_migrations.pgmigrations"),1);
    });
    await t.test("reader sees only published products and cannot fetch a known draft", async () => reader(async () => {
      assert.equal(await number("SELECT count(*) n FROM public.products"),2);
      assert.equal(await number("SELECT count(*) n FROM public.products WHERE id=106045"),0);
    }));
    await t.test("all product child tables hide draft rows", async () => reader(async () => {
      for (const table of ["product_attribute_values","product_features","product_search_documents","product_content_overrides"]) {
        assert.equal(await number(`SELECT count(*) n FROM public.${table} WHERE product_id=106045`),0);
      }
    }));
    await t.test("security-invoker content view handles overrides without leaking drafts", async () => reader(async () => {
      assert.equal(await number("SELECT count(*) n FROM public.product_content_sections WHERE product_id=100000"),13);
      assert.equal(await number("SELECT count(*) n FROM public.product_content_sections WHERE product_id=106045"),0);
      assert.equal(await number("SELECT count(*) n FROM public.category_content_templates WHERE category_id='KT0101'"),0);
    }));
    await t.test("metadata views and existing function are available to reader", async () => reader(async () => {
      assert.equal(await number("SELECT count(*) n FROM public.category_search_catalog"),3);
      assert.equal(await number("SELECT count(*) n FROM public.category_attribute_search_catalog"),1);
      assert.equal(await number("SELECT count(*) n FROM public.unit_conversion_rules"),1);
      assert.equal(await number("SELECT count(*) n FROM public.search_products(p_query=>'TEST-TV')"),1);
    }));
    await t.test("read-only probes reject private tables and writes without executing writes", async () => reader(async () => {
      await db.exec("BEGIN READ ONLY");
      try {
        for (const statement of ["EXPLAIN SELECT * FROM dataset_admin.import_runs",
          "EXPLAIN SELECT * FROM public.embedding_models","EXPLAIN SELECT * FROM public.product_embeddings",
          "EXPLAIN SELECT * FROM public.category_embeddings","EXPLAIN SELECT * FROM public.category_search_documents",
          "EXPLAIN UPDATE public.products SET name_th=name_th WHERE false","EXPLAIN DELETE FROM public.products WHERE false"]) {
          await db.exec("SAVEPOINT probe");
          await assert.rejects(db.exec(statement),(error:unknown) => typeof error==='object' && error!==null && 'code' in error && error.code==='42501');
          await db.exec("ROLLBACK TO SAVEPOINT probe; RELEASE SAVEPOINT probe");
        }
      } finally { await db.exec("ROLLBACK"); }
    }));
    await t.test("restrictive guard survives an accidentally broad permissive reader policy", async () => {
      await db.exec("CREATE POLICY unrelated_broad ON public.products FOR SELECT TO catalog_search_reader USING(true)");
      await reader(async () => assert.equal(await number("SELECT count(*) n FROM public.products"),2));
      await db.exec("DROP POLICY unrelated_broad ON public.products");
    });
    await t.test("checksum mismatch stops before changing permissions/history", async () => {
      await writeFile(join(directory,name),source+"\n-- changed\n");
      await assert.rejects(applyMigrations(client,directory,"up"),/checksum/);
      await writeFile(join(directory,name),source);
      assert.equal(await number("SELECT count(*) n FROM search_migrations.pgmigrations"),1);
    });
    await t.test("down removes only added access; original anon policy and all products survive", async () => {
      assert.deepEqual(await applyMigrations(client,directory,"down"),["0001_search_reader_access"]);
      assert.equal(await number("SELECT count(*) n FROM public.products"),4);
      await reader(async () => { await assert.rejects(db.query("SELECT * FROM public.products")); });
      await db.exec("SET ROLE anon");
      try { assert.equal(await number("SELECT count(*) n FROM public.products"),2); } finally { await db.exec("RESET ROLE"); }
    });
    await t.test("up after down reuses the same verified source", async () => {
      assert.deepEqual(await applyMigrations(client,directory,"up"),["0001_search_reader_access"]);
    });
    await t.test("failed migration rolls back SQL/history but retains checksum reservation", async () => {
      await writeFile(join(directory,"0002_failure.sql"),"-- Up Migration\nCREATE TABLE public.should_not_survive(id int); SELECT nonexistent_function();\n-- Down Migration\nDROP TABLE public.should_not_survive;");
      await assert.rejects(applyMigrations(client,directory,"up"));
      assert.equal((await db.query<{n:string|null}>("SELECT to_regclass('public.should_not_survive')::text n")).rows[0].n,null);
      assert.equal(await number("SELECT count(*) n FROM search_migrations.pgmigrations"),1);
      assert.equal(await number("SELECT count(*) n FROM search_migrations.checksums"),2);
    });
    console.log("Isolated SQL engine:",(await db.query<{version:string}>("SELECT version() version")).rows[0].version);
  } finally { await db.close(); await rm(directory,{recursive:true,force:true}); }
});
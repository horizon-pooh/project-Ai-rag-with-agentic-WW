import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp,readFile,copyFile,rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join,resolve } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import type { ClientBase,PoolClient } from "pg";
import { applyMigrations } from "../src/database/migrations.js";
import { createMetadataRepository } from "../src/repositories/metadata.repository.js";

test("metadata migration and read adapter on isolated PostgreSQL",async t => {
  const db=new PGlite();
  const directory=await mkdtemp(join(tmpdir(),"metadata-migration-"));
  const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]) {
    const sql=typeof query==="string"?query:query.text;
    const args=values ?? (typeof query==="object"?query.values:undefined);
    const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);
    return {rows:r?.rows ?? [],rowCount:r?.affectedRows ?? 0};
  }} as unknown as ClientBase;
  const metadata=createMetadataRepository(client as unknown as PoolClient);
  const scalar=async(sql:string) => (await db.query<{v:string}>(sql)).rows[0]!.v;
  const reader=async(action:()=>Promise<void>)=>{
    await db.exec("SET ROLE search_api");
    try {await action();} finally {await db.exec("RESET ROLE");}
  };
  try {
    await db.exec(await readFile(resolve("tests/fixtures/metadata.sql"),"utf8"));
    await copyFile(resolve("migrations/0002_metadata_helpers.sql"),join(directory,"0002_metadata_helpers.sql"));
    await t.test("real migration runner applies and reruns without reseeding",async()=>{
      assert.deepEqual(await applyMigrations(client,directory,"up"),["0002_metadata_helpers"]);
      assert.deepEqual(await applyMigrations(client,directory,"up"),[]);
    });
    await t.test("label normalization preserves decimals, punctuation and codes",async()=>{
      assert.equal(await scalar("SELECT catalog_search.normalize_label(E'  SCREEN\\tSize  ') v"),"screen size");
      assert.equal(await scalar("SELECT catalog_search.normalize_label(' 7.5 screen_size-OLED ') v"),"7.5 screen_size-oled");
    });
    await t.test("category candidates include exact IDs/names/aliases without hierarchy expansion",async()=>reader(async()=>{
      assert.equal((await metadata.findCategoryLabels(" TV "))[0]!.category_id,"TV0100");
      assert.equal((await metadata.findCategoryLabels("ทีวีOLED"))[0]!.category_id,"TV0108");
      assert.equal((await metadata.findCategoryLabels("tv0108"))[0]!.match_source,"id");
      assert.deepEqual(await metadata.findCategoryLabels("OLED television typo"),[]);
    }));
    await t.test("ambiguous attributes remain multiple, scoped and typed",async()=>reader(async()=>{
      const rows=await metadata.findAttributeLabels("TV0108","ขนาด");
      assert.equal(rows.length,2);
      const screen=rows.find(a=>a.attribute_id==="AT0048")!;
      assert.equal(screen.unit_code,"inch"); assert.equal(screen.min_value,"0");
      assert.equal(screen.max_value,null);
      assert.deepEqual(await metadata.findAttributeLabels("AP0101","ขนาดหน้าจอ"),[]);
      const attrs=await metadata.listCategoryAttributes("TV0108");
      assert.equal(attrs.find(a=>a.code==="package_width")!.is_filterable,false);
      assert.equal(attrs.find(a=>a.code==="quantum_dot")!.unit_code,null);
    }));
    await t.test("conversion uses explicit factors, offsets and decimal text",async()=>reader(async()=>{
      assert.equal((await metadata.convertUnit(10,"cm","mm")).value_out,"100");
      assert.equal((await metadata.convertUnit(55,"นิ้ว","inch")).value_out,"55");
      assert.equal((await metadata.convertUnit(32,"°C","°F")).value_out,"89.6");
      assert.equal((await metadata.convertUnit(1,"cm","inch")).value_out,"0.3937007874015748");
      assert.equal((await metadata.convertUnit(1,"inch","inch")).status,"unchanged");
    }));
    await t.test("missing, cross-dimension, unknown identity, case and disabled rules are explicit",async()=>{
      for(const [from,to,status] of [[null,"cm","missing_unit"],["kg","L","unsupported_unit"],
        ["fake","fake","unsupported_unit"],["CM","mm","unsupported_unit"],["cm","m","disabled_rule"]] as const){
        const r=await metadata.convertUnit(1,from,to);assert.equal(r.status,status);assert.equal(r.value_out,null);
      }
    });
    await t.test("bad requests fail before lookup; SQL independently rejects invalid input",async()=>{
      await assert.rejects(metadata.findCategoryLabels(" "));
      await assert.rejects(metadata.findCategoryLabels("a".repeat(201)));
      await assert.rejects(metadata.findAttributeLabels("bad'", "screen"));
      await assert.rejects(metadata.convertUnit(Number.NaN,"cm","mm"));
      await assert.rejects(db.query("SELECT * FROM catalog_search.convert_unit('Infinity'::numeric,'cm','mm')"));
      await assert.rejects(db.query("SELECT * FROM catalog_search.attribute_label_matches('bad','screen')"));
      assert.deepEqual(await metadata.findCategoryLabels("' OR 1=1 --"),[]);
    });
    await t.test("audit reports collisions and units without rules without inventing entries",async()=>{
      const a=await metadata.audit();assert.match(a.version,/^[a-f0-9]{64}$/);
      assert.equal(a.categories,4);assert.equal(a.attribute_collision_count,1);
      assert.deepEqual(a.unit_codes_without_rules,["W"]);
    });
    await t.test("version changes with metadata but is stable across timezone and timestamps",async()=>{
      const initial=await metadata.version();assert.equal(await metadata.version(),initial);
      await db.exec("SET TIME ZONE 'Asia/Bangkok'; UPDATE unit_conversion_rules SET updated_at=now()+interval '1 day'");
      assert.equal(await metadata.version(),initial);
      await db.exec("UPDATE attribute_definitions SET aliases=aliases||ARRAY['ขนาดใหม่'] WHERE id='AT0048'");
      assert.notEqual(await metadata.version(),initial);
      const changed=await metadata.version();
      await db.exec("UPDATE unit_conversion_rules SET rule_version='units-v2' WHERE from_unit='cm' AND to_unit='mm'");
      assert.notEqual(await metadata.version(),changed);
      const changed2=await metadata.version();
      await db.exec("UPDATE category_attributes SET is_filterable=false,max_value=900 WHERE attribute_id='AT0006'");
      assert.notEqual(await metadata.version(),changed2);
    });
    await t.test("reader executes invoker helpers, anon cannot, neither gains write access",async()=>{
      await reader(async()=>{
        assert.match(await metadata.version(),/^[a-f0-9]{64}$/);
        await assert.rejects(db.exec("UPDATE categories SET name_th=name_th"));
      });
      await db.exec("SET ROLE anon");
      try {await assert.rejects(metadata.version());} finally {await db.exec("RESET ROLE");}
    });
    await t.test("invalid stored identity is rejected rather than silently applied",async()=>{
      await db.exec("UPDATE unit_conversion_rules SET multiplier=2 WHERE from_unit='inch' AND to_unit='inch'");
      await assert.rejects(metadata.convertUnit(1,"inch","inch"));
      await db.exec("UPDATE unit_conversion_rules SET multiplier=1 WHERE from_unit='inch' AND to_unit='inch'");
    });
    await t.test("rollback preserves all source metadata and reapply restores helpers",async()=>{
      const version=await metadata.version();
      assert.deepEqual(await applyMigrations(client,directory,"down"),["0002_metadata_helpers"]);
      assert.equal(Number(await scalar("SELECT count(*)::text v FROM categories")),4);
      assert.equal(Number(await scalar("SELECT count(*)::text v FROM unit_conversion_rules")),11);
      assert.deepEqual(await applyMigrations(client,directory,"up"),["0002_metadata_helpers"]);
      assert.equal(await metadata.version(),version);
    });
  } finally {await db.close();await rm(directory,{recursive:true,force:true});}
});

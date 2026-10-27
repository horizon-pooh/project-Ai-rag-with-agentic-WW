import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import type {ClientBase,PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import {createCategoryResolutionRepository} from "../src/repositories/category-resolution.repository.js";
import {resolveCategoryQuery} from "../src/services/category-resolution.service.js";
import {createTypedFilterRepository} from "../src/repositories/typed-filter.repository.js";
import type {ToolSearchFilters} from "../src/types/tool-search.types.js";
import type {CategoryResolution} from "../src/types/category-resolution.types.js";

test("typed filters in SQL preserve scope, types, diagnostics and numeric precision",async t=>{
 const db=new PGlite({extensions:{pg_trgm}});const dir=await mkdtemp(join(tmpdir(),"typed-filters-"));const sqlCalls:string[]=[];
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
  const sql=typeof query==="string"?query:query.text;sqlCalls.push(sql);
  const args=values ?? (typeof query==="object"?query.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows ?? [],rowCount:r?.affectedRows ?? 0};
 }} as unknown as ClientBase;
 const repo=createTypedFilterRepository(client as unknown as PoolClient);
 const categories=createCategoryResolutionRepository(client as unknown as PoolClient);
 let tv:CategoryResolution;let washer:CategoryResolution;let both:CategoryResolution;
 const run=(filters:ToolSearchFilters,scope=tv)=>repo.resolve(scope,filters);
 const reader=async(action:()=>Promise<void>)=>{await db.exec("SET ROLE search_api");try{await action();}finally{await db.exec("RESET ROLE");}};
 try{
  for(const name of ["metadata.sql","category-resolution.sql","typed-filters.sql"])
    await db.exec(await readFile(resolve("tests/fixtures",name),"utf8"));
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions;GRANT USAGE ON SCHEMA extensions TO catalog_search_reader");
  for(const name of ["0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql"])
    await copyFile(resolve("migrations",name),join(dir,name));
  await t.test("migration applies with existing metadata and real runner",async()=>{
    assert.deepEqual(await applyMigrations(client,dir,"up"),["0002_metadata_helpers","0003_category_resolution","0004_typed_filter_resolution"]);
    assert.deepEqual(await applyMigrations(client,dir,"up"),[]);
    tv=await resolveCategoryQuery(categories,"TV0108");washer=await resolveCategoryQuery(categories,"AP0101");
    both=await resolveCategoryQuery(categories,"TV0108 | AP0101");
  });
  for(const query of ["ขนาดหน้าจอ","screen_size","AT0048"]){
    await t.test(`numeric exact attribute ${query}`,async()=>reader(async()=>{
      const r=await run({specs:[{attribute_query:query,op:"eq",value:55}]});const s=r.branches[0]!.resolved_specs[0]!;
      assert.equal(r.status,"resolved");assert.equal(s.attribute_id,"AT0048");assert.equal(s.numeric_value,"55");
      assert.equal(s.unit_code,"inch");assert.equal(s.unit_mode,"assumed_canonical");assert.equal(s.comparison_epsilon,"0");
      assert.equal(s.path,"/filters/specs/0");
    }));
  }
  for(const [value,expected] of [[0,"0"],[7.5,"7.5"],[-0,"0"],[1e21,"1000000000000000000000"]] as const){
    await t.test(`numeric value retained ${value}`,async()=>assert.equal((await run({specs:[{attribute_query:"width",op:"gte",value}]})).branches[0]!.resolved_specs[0]!.numeric_value,expected));
  }
  for(const value of [false,true]){
    await t.test(`boolean ${value} remains boolean`,async()=>{
      const s=(await run({specs:[{attribute_query:"quantum_dot",op:"eq",value}]})).branches[0]!.resolved_specs[0]!;
      assert.equal(s.boolean_value,value);assert.equal(s.numeric_value,null);
    });
  }
  for(const value of ["ไม้โอ๊ค","55","true","001","Steel"]){
    await t.test(`text preserved without guessing or normalization: ${value}`,async()=>assert.equal((await run({specs:[{attribute_query:"วัสดุ",op:"eq",value}]})).branches[0]!.resolved_specs[0]!.text_value,value));
  }
  for(const [query,value] of [["screen_size","55"],["quantum_dot","false"],["main_material",55]] as const){
    await t.test(`metadata type mismatch: ${query}/${value}`,async()=>{
      const spec=typeof value==="number"?{attribute_query:query,op:"eq" as const,value}:{attribute_query:query,op:"eq" as const,value};
      const r=await run({specs:[spec]});assert.equal(r.all_requested_filters_applied,false);
      assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,"data_type_mismatch");assert.deepEqual(r.branches[0]!.resolved_specs,[]);
    });
  }
  for(const unit of ["inch","นิ้ว"," inch "]){
    await t.test(`canonical unit/label ${unit}`,async()=>{
      const s=(await run({specs:[{attribute_query:"screen_size",op:"eq",value:55,unit}]})).branches[0]!.resolved_specs[0]!;
      assert.equal(s.numeric_value,"55");assert.equal(s.unit_mode,"canonical_or_label");assert.equal(s.comparison_epsilon,"0");
    });
  }
  await t.test("explicit conversion preserves decimal output and precision policy",async()=>{
    const s=(await run({specs:[{attribute_query:"width",op:"eq",value:125,unit:"mm"}]})).branches[0]!.resolved_specs[0]!;
    assert.equal(s.numeric_value,"12.5");assert.equal(s.rule_version,"units-v1");assert.equal(s.unit_mode,"converted");
    assert.ok(Number(s.comparison_epsilon)>0);assert.equal(s.requested_value,125);assert.equal(s.requested_unit,"mm");
  });
  await t.test("approximate inverse does not make equivalent eq constraints conflict",async()=>{
    const r=await run({specs:[{attribute_query:"screen_size",op:"eq",value:55,unit:"inch"},
      {attribute_query:"screen_size",op:"eq",value:139.7,unit:"cm"}]});
    assert.equal(r.status,"resolved");assert.deepEqual(r.branches[0]!.conflicts,[]);
    assert.equal(r.branches[0]!.resolved_specs[1]!.numeric_value,"54.99999999999999956");
  });
  for(const [query,unit,reason,scope] of [["screen_size","kg","unsupported_unit","tv"],["width","CM","unsupported_unit","tv"],
    ["width","m","unsupported_unit","tv"],["power","kg","unsupported_unit","washer"]] as const){
    await t.test(`unsupported conversion ${query}/${unit}`,async()=>{
      const r=await run({specs:[{attribute_query:query,op:"eq",value:1,unit}]},scope==="tv"?tv:washer);
      assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,reason);assert.equal(r.all_requested_filters_applied,false);
    });
  }
  await t.test("known unit with no conversion whitelist is still canonical for its own attribute",async()=>{
    for(const unit of ["W","วัตต์"]){const r=await run({specs:[{attribute_query:"power",op:"eq",value:100,unit}]},washer);
      assert.equal(r.branches[0]!.resolved_specs[0]!.numeric_value,"100");}
    const doors=await run({specs:[{attribute_query:"ประตู",op:"eq",value:2,unit:"ประตู"}]},washer);
    assert.equal(doors.branches[0]!.resolved_specs[0]!.unit_code,"door");
  });
  await t.test("disabled conversion remains unapplied",async()=>{
    await db.exec("UPDATE unit_conversion_rules SET enabled=false WHERE from_unit='mm' AND to_unit='cm'");
    assert.equal((await run({specs:[{attribute_query:"width",op:"eq",value:100,unit:"mm"}]})).branches[0]!.unapplied_filters[0]!.reason,"disabled_rule");
    await db.exec("UPDATE unit_conversion_rules SET enabled=true WHERE from_unit='mm' AND to_unit='cm'");
  });
  await t.test("ambiguous spec requires clarification and retains IDs and original path",async()=>{
    const r=await run({specs:[{attribute_query:"ขนาด",op:"eq",value:55}]});assert.equal(r.status,"needs_clarification");
    assert.equal(r.branches[0]!.unapplied_filters[0]!.path,"/filters/specs/0");assert.equal(r.branches[0]!.unapplied_filters[0]!.candidates.length,2);
  });
  await t.test("package width cannot silently replace filterable product width",async()=>{
    const r=await run({specs:[{attribute_query:"package_width",op:"eq",value:55}]});
    assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,"attribute_not_filterable");
  });
  await t.test("multi-category specs are resolved separately and unknown is never zero",async()=>{
    const r=await run({specs:[{attribute_query:"screen_size",op:"eq",value:55}]},both);
    assert.equal(r.branches.find(b=>b.category_id==="AP0101")!.status,"partial");
    assert.equal(r.branches.find(b=>b.category_id==="TV0108")!.resolved_specs[0]!.numeric_value,"55");
    assert.equal(r.all_requested_filters_applied,false);
  });
  await t.test("numeric request contradiction is detected with every source path",async()=>{
    const r=await run({specs:[{attribute_query:"width",op:"gte",value:100},{attribute_query:"ความกว้าง",op:"lte",value:50}]});
    assert.equal(r.status,"constraint_conflict");assert.deepEqual(r.branches[0]!.conflicts[0]!.paths,["/filters/specs/0","/filters/specs/1"]);
  });
  for(const [query,a,b] of [["main_material","Wood","Steel"],["quantum_dot",true,false]] as const){
    await t.test(`conflicting exact values ${query}`,async()=>{
      assert.equal((await run({specs:[{attribute_query:query,op:"eq",value:a},{attribute_query:query,op:"eq",value:b}]})).status,"constraint_conflict");
    });
  }
  await t.test("single bound and identical equalities are not conflicts",async()=>{
    const r=await run({specs:[{attribute_query:"width",op:"gte",value:55},{attribute_query:"width",op:"eq",value:55},{attribute_query:"width",op:"eq",value:55}]});
    assert.equal(r.status,"resolved");
  });
  await t.test("metadata bounds warn but do not pretend to prove product inventory",async()=>{
    const r=await run({specs:[{attribute_query:"width",op:"eq",value:999}]});assert.equal(r.status,"resolved");
    assert.equal(r.branches[0]!.resolved_specs[0]!.outside_documented_range,true);
  });
  await t.test("brand name, Thai name, aliases and IDs are OR with positions preserved",async()=>{
    const r=await run({brand_queries:["Bosch","แอลจี","BR0001"]});assert.deepEqual(r.branches[0]!.brand_ids,["BR0001","BR0002"]);
    assert.equal(r.branches[0]!.resolved_brands.length,3);assert.equal(r.branches[0]!.resolved_brands[2]!.path,"/filters/brand_queries/2");
  });
  for(const [query,reason] of [["unknown-brand-xyz","unknown_brand"],["แบรนด์ร่วม","ambiguous_brand"]] as const){
    await t.test(`unresolved brand ${query} blocks silent OR relaxation`,async()=>{
      const r=await run({brand_queries:["LG",query]});assert.equal(r.status,"needs_clarification");
      assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,reason);
    });
  }
  await t.test("features all mean AND, category scoped, with positions preserved",async()=>{
    const r=await run({feature_queries_all:["HDMI","variable_refresh"]});assert.deepEqual(r.branches[0]!.feature_ids_all,["FT0023","FT0074"]);
    assert.equal(r.branches[0]!.resolved_features[1]!.path,"/filters/feature_queries_all/1");
    const absent=await run({feature_queries_all:["HDMI"]},washer);assert.equal(absent.branches[0]!.unapplied_filters[0]!.reason,"feature_not_in_category");
  });
  await t.test("ambiguous feature never selects first row",async()=>assert.equal((await run({feature_queries_all:["ฟีเจอร์ร่วม"]})).status,"needs_clarification"));
  await t.test("price zero survives and decimals are text internally",async()=>{
    const r=await run({price:{min:0,max:25000.5}});assert.deepEqual(r.branches[0]!.price,{min:"0",max:"25000.5",currency:"THB"});
  });
  await t.test("input is not mutated and single SQL call is parameterized",async()=>{
    const filters={specs:[{attribute_query:"width",op:"eq" as const,value:10}]};const copy=structuredClone(filters);sqlCalls.length=0;
    await run(filters);assert.deepEqual(filters,copy);assert.equal(sqlCalls.length,1);assert.ok(sqlCalls[0]!.includes("$2::jsonb"));
  });
  await t.test("unknown names and injection strings are diagnostic, not executable SQL",async()=>{
    const r=await run({specs:[{attribute_query:"'; DROP TABLE brands; --",op:"eq",value:1}]});assert.equal(r.all_requested_filters_applied,false);
    assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM brands")).rows[0]!.n,4);
  });
  const badInputs=[null,[],{evil:1},{specs:null},{specs:[{}]},
    {specs:[{attribute_query:"width",op:"gte",value:"55"}]},
    {specs:[{attribute_query:"quantum_dot",op:"gte",value:false}]},
    {specs:[{attribute_query:"width",op:"eq",value:1,unit:null}]},
    {specs:[{attribute_query:"width",op:"eq",value:null}]},{price:{min:"10"}},{price:{min:10,max:0}},{price:{currency:"USD"}},
    {brand_queries:[1]},{feature_queries_all:"HDMI"},{specs:Array(21).fill({attribute_query:"width",op:"eq",value:1})}];
  for(const [i,input] of badInputs.entries()){
    await t.test(`SQL independently rejects invalid JSON ${i}`,async()=>{
      await assert.rejects(db.query("SELECT catalog_search.resolve_typed_filters(ARRAY['TV0108'],$1::jsonb)",[JSON.stringify(input)]),
        (e:unknown)=>typeof e==="object" && e!==null && "code" in e && e.code==="22023");
    });
  }
  await t.test("adapter rejects invalid source values before querying",async()=>{
    await assert.rejects(run({specs:[{attribute_query:"width",op:"eq",value:Infinity}]}));
    await assert.rejects(run({price:{min:1,max:0}}));
    await assert.rejects(run({category_query:"other"}));
    await assert.rejects(repo.resolve({...tv,status:"needs_clarification"},{}));
    await assert.rejects(db.query("SELECT catalog_search.resolve_typed_filters(ARRAY['TV0100'],'{}')"));
  });
  await t.test("unknown explicit IDs cannot be redirected by metadata aliases",async()=>{
    await db.exec("UPDATE brands SET aliases=aliases||'[\"BR9999\"]'::jsonb WHERE id='BR0002'; UPDATE features SET aliases=aliases||'[\"FT9999\"]'::jsonb WHERE id='FT0023'; UPDATE attribute_definitions SET aliases=aliases||ARRAY['AT9999'] WHERE id='AT0048'");
    const b=await run({brand_queries:["BR9999"]});assert.equal(b.status,"needs_clarification");assert.deepEqual(b.branches[0]!.brand_ids,[]);
    const f=await run({feature_queries_all:["FT9999"]});assert.deepEqual(f.branches[0]!.feature_ids_all,[]);
    const a=await run({specs:[{attribute_query:"AT9999",op:"eq",value:55}]});assert.deepEqual(a.branches[0]!.resolved_specs,[]);
  });
  await t.test("converted constraints conflict only when canonical intervals cannot overlap",async()=>{
    const yes=await run({specs:[{attribute_query:"width",op:"gte",value:100,unit:"mm"},{attribute_query:"width",op:"lte",value:9,unit:"cm"}]});
    assert.equal(yes.status,"constraint_conflict");
    const no=await run({specs:[{attribute_query:"width",op:"gte",value:100,unit:"mm"},{attribute_query:"width",op:"lte",value:10,unit:"cm"}]});
    assert.equal(no.status,"resolved");
  });
  await t.test("kg and liters never substitute for a washer capacity",async()=>{
    const r=await run({specs:[{attribute_query:"wash_capacity",op:"eq",value:8,unit:"L"}]},washer);
    assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,"unsupported_unit");
  });
  await t.test("reader can resolve and anon cannot; source writes remain forbidden",async()=>{
    await reader(async()=>{await run({brand_queries:["LG"]});await assert.rejects(db.exec("UPDATE brands SET name=name"));});
    await db.exec("SET ROLE anon");try{await assert.rejects(run({}));}finally{await db.exec("RESET ROLE");}
  });
  await t.test("rollback preserves metadata and prior resolvers then reapply works",async()=>{
    assert.deepEqual(await applyMigrations(client,dir,"down"),["0004_typed_filter_resolution"]);
    assert.equal((await resolveCategoryQuery(categories,"OLED")).status,"resolved");
    assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM features")).rows[0]!.n,4);
    assert.deepEqual(await applyMigrations(client,dir,"up"),["0004_typed_filter_resolution"]);assert.equal((await run({})).status,"resolved");
  });
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

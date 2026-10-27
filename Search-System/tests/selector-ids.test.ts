import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import type {ClientBase,PoolClient} from "pg";
import {parseToolSearchRequest} from "../src/validation/tool-search.validation.js";
import type {ToolSearchFilters} from "../src/types/tool-search.types.js";
import {applyMigrations} from "../src/database/migrations.js";
import {resolveCategoryFilters} from "../src/services/category-selector.service.js";
import {createTypedFilterRepository} from "../src/repositories/typed-filter.repository.js";

for(const [name,input] of Object.entries({
 categoryOnly:{filters:{category_ids:["TV0108"]}},featureOnly:{filters:{feature_ids_all:["FT0023"]}},
 numericId:{filters:{specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"}]}},
 booleanId:{filters:{specs:[{attribute_id:"AT0093",op:"eq",value:false}]}},
 textId:{filters:{specs:[{attribute_id:"AT0014",op:"eq",value:"001"}]}},
 both:{filters:{category_query:"ทีวี OLED",category_ids:["TV0108"],feature_ids_all:["FT0023"],
 specs:[{attribute_query:"ขนาดหน้าจอ",attribute_id:"AT0048",op:"eq",value:55}]}}
}))test(`ID request contract accepts ${name} without mutation`,()=>{
 const saved=structuredClone(input);const result=parseToolSearchRequest(input);assert.equal(result.valid,true);assert.deepEqual(input,saved);
});
const invalid=[{category_ids:[]},{category_ids:["tv0108"]},{category_ids:[100000]},{category_ids:["TV0108","TV0108"]},
 {category_ids:Array(21).fill("TV0108")},{category_ids:["TV0108'; --"]},{feature_ids_all:[]},{feature_ids_all:["AT0048"]},
 {feature_ids_all:["FT0023","FT0023"]},{feature_ids_all:[23]},{specs:[{attribute_id:"AT0048",op:"gte",value:"55"}]},
 {specs:[{attribute_id:"AT0093",op:"eq",value:false,unit:"x"}]},{specs:[{attribute_id:"BR0001",op:"eq",value:1}]},
 {specs:[{attribute_id:null,attribute_query:"width",op:"eq",value:1}]},{specs:[{op:"eq",value:1}]},
 {specs:[{attribute_id:"AT0048",attribute_query:"",op:"eq",value:1}]},{category_id:"TV0108"},{feature_ids_any:["FT0023"]}];
for(const [i,filters]of invalid.entries())test(`ID contract rejects malformed input ${i}`,()=>assert.equal(parseToolSearchRequest({query:"ทีวี",filters}).valid,false));

test("verified IDs, mixed selectors, compatibility and rollback in PostgreSQL",async t=>{
 const db=new PGlite({extensions:{pg_trgm}});const dir=await mkdtemp(join(tmpdir(),"selector-ids-"));const calls:string[]=[];
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
  const sql=typeof query==="string"?query:query.text;calls.push(sql);const args=values ?? (typeof query==="object"?query.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows ?? [],rowCount:r?.affectedRows ?? 0};
 }} as unknown as ClientBase;
 const queryClient=client as unknown as PoolClient;const typed=createTypedFilterRepository(queryClient);
 const scope=(filters:ToolSearchFilters)=>resolveCategoryFilters(queryClient,filters);
 const run=async(filters:ToolSearchFilters)=>{const c=await scope(filters);assert.equal(c.status,"resolved");return typed.resolve(c,filters);};
 const legacy:ToolSearchFilters={category_query:"ทีวี OLED",brand_queries:["LG"],feature_queries_all:["HDMI"],
 specs:[{attribute_query:"ขนาดหน้าจอ",op:"eq",value:55,unit:"นิ้ว"}],price:{max:25000}};
 try{
  for(const name of ["metadata.sql","category-resolution.sql","typed-filters.sql"])
   await db.exec(await readFile(resolve("tests/fixtures",name),"utf8"));
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions;GRANT USAGE ON SCHEMA extensions TO catalog_search_reader");
  for(const name of ["0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql"])
   await copyFile(resolve("migrations",name),join(dir,name));
  await applyMigrations(client,dir,"up");const before=await run(legacy);
  await copyFile(resolve("migrations/0005_verified_selector_ids.sql"),join(dir,"0005_verified_selector_ids.sql"));
  await t.test("new migration applies through real runner and reruns no-op",async()=>{
   assert.deepEqual(await applyMigrations(client,dir,"up"),["0005_verified_selector_ids"]);assert.deepEqual(await applyMigrations(client,dir,"up"),[]);
  });
  await t.test("legacy JSON follows exactly the old path and result",async()=>{
   calls.length=0;assert.deepEqual(await run(legacy),before);assert.ok(calls.at(-1)!.includes("resolve_typed_filters("));
  });
  await t.test("screenshot JSON plus optional IDs retains price/page/spec behavior",async()=>{
   const input=JSON.parse(await readFile("docs/examples/tool-search-v1/request-with-ids.json","utf8"));assert.equal(parseToolSearchRequest(input).valid,true);
   const r=await run(input.filters);assert.equal(r.status,"resolved");assert.equal(r.resolver_version,"typed-filters-ids-v1");
   assert.equal(r.branches[0]!.resolved_specs[0]!.numeric_value,"55");assert.equal(r.branches[0]!.price.max,"25000");
  });
  await t.test("ID-only path has two parameterized statements and no lexical candidate request",async()=>{
   calls.length=0;const r=await run({category_ids:["TV0108"],feature_ids_all:["FT0023"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]});
   assert.equal(r.status,"resolved");assert.equal(calls.length,2);assert.ok(calls[0]!.includes("category_id_scope"));assert.ok(calls[1]!.includes("resolve_typed_filters_v2"));
  });
  await t.test("direct IDs genuinely work with name lookup functions unavailable",async()=>{
   const functions=[["category_candidates(text,integer)","category_candidates"],["attribute_label_matches(text,text)","attribute_label_matches"],["filter_name_candidates(text,text,text)","filter_name_candidates"]];
   for(const [signature,name]of functions)await db.exec(`ALTER FUNCTION catalog_search.${signature} RENAME TO ${name}_probe`);
   try{const r=await run({category_ids:["TV0108"],feature_ids_all:["FT0023"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]});assert.equal(r.status,"resolved");}
   finally{for(const [signature,name]of functions)await db.exec(`ALTER FUNCTION catalog_search.${name}_probe${signature!.slice(signature!.indexOf("("))} RENAME TO ${name}`);}
  });
  await t.test("parent IDs expand and overlapping parent/leaf IDs do not duplicate categories",async()=>{
   const r=await scope({category_ids:["TV0100","TV0108"]});assert.equal(r.status,"resolved");assert.deepEqual(r.category_ids,["TV0107","TV0108","TV0109"]);
  });
  await t.test("broad name can be narrowed by compatible IDs",async()=>{
   const r=await scope({category_query:"ทีวี",category_ids:["TV0108"]});assert.equal(r.status,"resolved");assert.deepEqual(r.category_ids,["TV0108"]);
  });
  await t.test("OLED name with QLED ID is a conflict without partial selection",async()=>{
   const r=await scope({category_query:"ทีวี OLED",category_ids:["TV0109"]});assert.equal(r.status,"constraint_conflict");
   assert.deepEqual(r.category_ids,[]);assert.equal(r.selector_issues![0]!.path,"/filters/category_ids");
  });
  for(const ids of [["ZX9999"],["TV0108","ZX9999"]])await t.test(`unknown category ID cannot fall back or drop a branch ${ids.join()}`,async()=>{
   const r=await scope({category_query:"ทีวี OLED",category_ids:ids});assert.equal(r.status,"needs_clarification");assert.deepEqual(r.category_ids,[]);
  });
  await t.test("unknown name alongside known category ID is not ignored",async()=>{
   assert.equal((await scope({category_query:"xyzzyx",category_ids:["TV0108"]})).status,"needs_clarification");
  });
  await t.test("missing category selector does not search all categories",async()=>assert.equal((await scope({feature_ids_all:["FT0023"]})).status,"needs_clarification"));
  await t.test("empty parent ID scope is diagnosed",async()=>{
   const r=await scope({category_ids:["EM0000"]});assert.equal(r.status,"needs_clarification");assert.equal(r.selector_issues![0]!.reason,"empty_category_scope");
  });
  await t.test("attribute ID plus ambiguous name can disambiguate only a matching candidate",async()=>{
   const r=await run({category_ids:["TV0108"],specs:[{attribute_id:"AT0048",attribute_query:"ขนาด",op:"eq",value:55}]});
   assert.equal(r.status,"resolved");assert.equal(r.branches[0]!.resolved_specs[0]!.attribute_id,"AT0048");
  });
  await t.test("attribute ID and contradictory name do not silently prefer ID",async()=>{
   const r=await run({category_ids:["TV0108"],specs:[{attribute_id:"AT0048",attribute_query:"width",op:"eq",value:55}]});
   assert.equal(r.status,"constraint_conflict");assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,"attribute_id_name_mismatch");
  });
  await t.test("unknown attribute IDs require clarification, never zero or ignored constraint",async()=>{
   const r=await run({category_ids:["TV0108"],specs:[{attribute_id:"AT9999",op:"eq",value:55}]});
   assert.equal(r.status,"needs_clarification");assert.equal(r.branches[0]!.unapplied_filters[0]!.path,"/filters/specs/0/attribute_id");
  });
  await t.test("attribute ID outside a category makes only that OR branch impossible",async()=>{
   const r=await run({category_ids:["TV0108","AP0101"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]});
   assert.equal(r.status,"resolved");assert.equal(r.branches.find(b=>b.category_id==="AP0101")!.status,"constraint_conflict");
   assert.equal(r.branches.find(b=>b.category_id==="TV0108")!.status,"ready");
  });
  await t.test("explicit feature ID absent from category cannot be silently relaxed",async()=>{
   const r=await run({category_ids:["AP0101"],feature_ids_all:["FT0023"]});assert.equal(r.status,"constraint_conflict");
   assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,"feature_id_not_in_category");
  });
  await t.test("unknown feature ID blocks search and keeps original pointer",async()=>{
   const r=await run({category_ids:["TV0108"],feature_ids_all:["FT0023","FT9999"]});assert.equal(r.status,"needs_clarification");
   assert.equal(r.branches[0]!.unapplied_filters[0]!.path,"/filters/feature_ids_all/1");assert.equal(r.all_requested_filters_applied,false);
  });
  await t.test("feature IDs and names deduplicate the same AND constraints, regardless of order",async()=>{
   const r=await run({category_ids:["TV0108"],feature_ids_all:["FT0074","FT0023"],feature_queries_all:["HDMI","VRR"]});
   assert.equal(r.status,"resolved");assert.deepEqual(r.branches[0]!.feature_ids_all,["FT0023","FT0074"]);
   assert.equal(r.branches[0]!.resolved_features.length,4);
  });
  await t.test("different feature name/ID lists mean additional AND requirements, not index pairs",async()=>{
   const r=await run({category_ids:["TV0108"],feature_ids_all:["FT0074"],feature_queries_all:["HDMI"]});assert.equal(r.status,"resolved");
   assert.deepEqual(r.branches[0]!.feature_ids_all,["FT0023","FT0074"]);
  });
  await t.test("unknown feature name paired with valid ID needs confirmation",async()=>{
   assert.equal((await run({category_ids:["TV0108"],feature_ids_all:["FT0023"],feature_queries_all:["unknownxyz"]})).status,"needs_clarification");
  });
  for(const [id,value,reason]of [["AT0048","55","data_type_mismatch"],["AT0010",10,"attribute_not_filterable"]]as const)
   await t.test(`an explicit unusable attribute ID is never relaxed: ${id}`,async()=>{
    const spec=typeof value==="number"?{attribute_id:id,op:"eq" as const,value}:{attribute_id:id,op:"eq" as const,value};
    const r=await run({category_ids:["TV0108"],specs:[spec]});assert.equal(r.status,"needs_clarification");assert.equal(r.branches[0]!.unapplied_filters[0]!.reason,reason);
   });
  await t.test("false, decimal and unit conversion policies survive direct IDs",async()=>{
   const r=await run({category_ids:["TV0108"],specs:[{attribute_id:"AT0093",op:"eq",value:false},{attribute_id:"AT0006",op:"eq",value:125,unit:"mm"}]});
   assert.equal(r.branches[0]!.resolved_specs[0]!.boolean_value,false);assert.equal(r.branches[0]!.resolved_specs[1]!.numeric_value,"12.5");
   assert.ok(Number(r.branches[0]!.resolved_specs[1]!.comparison_epsilon)>0);
  });
  await t.test("numeric contradictions still fail on direct IDs",async()=>{
   const r=await run({category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"gte",value:65},{attribute_id:"AT0048",op:"lte",value:55}]});assert.equal(r.status,"constraint_conflict");
  });
  await t.test("old spec names can be mixed with ID-only specs",async()=>{
   const r=await run({category_ids:["TV0108"],specs:[{attribute_query:"width",op:"gte",value:0},{attribute_id:"AT0048",op:"eq",value:55}]});assert.equal(r.status,"resolved");assert.equal(r.branches[0]!.resolved_specs.length,2);
  });
  await t.test("repository rejects category IDs unrelated to verified scope",async()=>{
   const c=await scope({category_ids:["TV0108"]});await assert.rejects(typed.resolve(c,{category_ids:["TV0109"]}));
   const r=await db.query<{r:{status:string}}>("SELECT catalog_search.resolve_typed_filters_v2(ARRAY['TV0108'],$1::jsonb) r",[JSON.stringify({category_ids:["TV0109"]})]);
   assert.equal(r.rows[0]!.r.status,"constraint_conflict");
  });
  for(const [i,filters]of invalid.entries())await t.test(`SQL independently rejects malformed ID JSON ${i}`,async()=>{
   await assert.rejects(db.query("SELECT catalog_search.resolve_typed_filters_v2(ARRAY['TV0108'],$1::jsonb)",[JSON.stringify(filters)]),
    (e:unknown)=>typeof e==="object"&&e!==null&&"code"in e&&e.code==="22023");
  });
  await t.test("reader executes new helpers without acquiring source write rights; anon denied",async()=>{
   await db.exec("SET ROLE search_api");try{assert.equal((await run({category_ids:["TV0108"]})).status,"resolved");await assert.rejects(db.exec("UPDATE features SET name_th=name_th"));}finally{await db.exec("RESET ROLE");}
   await db.exec("SET ROLE anon");try{await assert.rejects(scope({category_ids:["TV0108"]}));}finally{await db.exec("RESET ROLE");}
  });
  await t.test("rollback leaves legacy behavior/data intact and reapply restores IDs",async()=>{
   assert.deepEqual(await applyMigrations(client,dir,"down"),["0005_verified_selector_ids"]);assert.deepEqual(await run(legacy),before);
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM features")).rows[0]!.n,4);
   assert.deepEqual(await applyMigrations(client,dir,"up"),["0005_verified_selector_ids"]);assert.equal((await run({category_ids:["TV0108"]})).status,"resolved");
  });
 }finally{await db.close();await rm(dir,{recursive:true,force:true});}
});

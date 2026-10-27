import assert from "node:assert/strict";
import {test} from "node:test";
import {readFile,mkdtemp,copyFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {PGlite} from "@electric-sql/pglite";
import {pg_trgm} from "@electric-sql/pglite/contrib/pg_trgm";
import type {ClientBase,PoolClient} from "pg";
import {applyMigrations} from "../src/database/migrations.js";
import {parseToolSearchRequest} from "../src/validation/tool-search.validation.js";
import {resolveCategoryFilters} from "../src/services/category-selector.service.js";
import type {ToolSearchRequest} from "../src/types/tool-search.types.js";
import type {KeywordSearchEnvelope} from "../src/types/keyword-search.types.js";

test("keyword SQL search: scoped facts, explained alternatives, identity and paging",async t=>{
 const db=new PGlite({extensions:{pg_trgm}});const directory=await mkdtemp(join(tmpdir(),"keyword-search-"));
 const client={async query(query:string|{text:string;values?:unknown[]},values?:unknown[]){
  const sql=typeof query==="string"?query:query.text;const args=values ?? (typeof query==="object"?query.values:undefined);
  const r=args?.length?await db.query(sql,args):(await db.exec(sql)).at(-1);return {rows:r?.rows ?? [],rowCount:r?.affectedRows ?? 0};
 }} as unknown as ClientBase;
 const raw=async(input:unknown,categories=["TV0108"])=>(await db.query<{result:KeywordSearchEnvelope}>(
  "SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb) AS result",[categories,JSON.stringify(input)])).rows[0]!.result;
 const run=async(input:ToolSearchRequest={})=>{
  const parsed=parseToolSearchRequest({filters:{category_ids:["TV0108"]},...input});assert.equal(parsed.valid,true);
  if(!parsed.valid)throw new Error("Bad test request");
  const scope=await resolveCategoryFilters(client as unknown as PoolClient,parsed.value.filters);assert.equal(scope.status,"resolved");
  return raw(parsed.value,scope.category_ids);
 };
 const reader=async(action:()=>Promise<void>)=>{await db.exec("SET ROLE search_api");try{await action();}finally{await db.exec("RESET ROLE");}};
 const idList=(r:KeywordSearchEnvelope)=>r.results.map(p=>p.article_id);
 const strictIds=(r:KeywordSearchEnvelope)=>r.results.filter(p=>p.match_type==="matches_applied_filters").map(p=>p.article_id);
 try{
  for(const name of ["metadata.sql","category-resolution.sql","typed-filters.sql","keyword-products.sql"])
   await db.exec(await readFile(resolve("tests/fixtures",name),"utf8"));
  await db.exec("CREATE SCHEMA extensions;CREATE EXTENSION pg_trgm WITH SCHEMA extensions");
  for(const name of ["0001_search_reader_access.sql","0002_metadata_helpers.sql","0003_category_resolution.sql","0004_typed_filter_resolution.sql","0005_verified_selector_ids.sql","0006_keyword_product_search.sql"])
   await copyFile(resolve("migrations",name),join(directory,name));
  await t.test("all migrations apply against independent schema with real runner",async()=>{
   assert.deepEqual(await applyMigrations(client,directory,"up"),["0001_search_reader_access","0002_metadata_helpers","0003_category_resolution","0004_typed_filter_resolution","0005_verified_selector_ids","0006_keyword_product_search"]);
   assert.deepEqual(await applyMigrations(client,directory,"up"),[]);
  });
  await t.test("filter-only lists published selected categories; mock notices survive",async()=>reader(async()=>{
   const r=await run({sort:"id",limit:100});assert.equal(r.status,"ok");assert.equal(r.meta!.total_matches,11);assert.equal(r.meta!.total_alternatives,0);
   assert.ok(r.results.every(p=>p.category_id==="TV0108" && p.is_mock && p.mock_notice));assert.ok(!idList(r).includes(106045));
  }));
  for(const query of ["ทีวี","TV","โทรทัศน์","Television","OLED","ทีวี OLED"])
   await t.test(`keyword names/English/aliases ${query}`,async()=>{const r=await run({query,limit:100});assert.equal(r.meta!.total_matches,11);});
  await t.test("usage words come from keyword documents, not fabricated semantic matches",async()=>{
   const r=await run({query:"ดูหนัง",limit:100});assert.deepEqual(strictIds(r),[100000,100006]);assert.equal(r.semantic_available,false);
   assert.ok(r.warnings.some(w=>w.code==="semantic_unavailable"));
  });
  await t.test("all keyword tokens must match; English core FTS is literal-safe",async()=>{
   const r=await run({query:"LG OLED",limit:100});assert.equal(r.meta!.total_matches,10);
   assert.ok(!strictIds(r).includes(100002));
  });
  await t.test("unknown keywords produce honest query alternatives within the chosen category",async()=>{
   const r=await run({query:"xyzzyxunknown",limit:100});assert.equal(r.meta!.total_matches,0);assert.equal(r.meta!.total_alternatives,11);
   assert.ok(r.results.every(p=>p.unmet_filters.some(d=>d.path==="/query" && d.reason==="keyword_not_matched")));
  });
  for(const query of ["%","_","'; DROP TABLE products; --","OLED | QLED"])
   await t.test(`query syntax remains data: ${query}`,async()=>{
    const r=await run({query});assert.equal(r.meta!.total_matches,0);
    assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM products")).rows[0]!.n,16);
   });
  await t.test("known exact filters precede every explained alternative",async()=>reader(async()=>{
   const r=await run({query:"ทีวี",filters:{category_ids:["TV0108"],brand_queries:["LG"],price:{max:25000},feature_ids_all:["FT0023"],
     specs:[{attribute_id:"AT0048",op:"eq",value:55,unit:"นิ้ว"},{attribute_id:"AT0093",op:"eq",value:false}]},limit:100});
   assert.deepEqual(strictIds(r),[100000,100004,100006]);assert.equal(r.meta!.total_alternatives,8);
   let alternative=false;for(const p of r.results){
    if(p.match_type==="alternative"){alternative=true;assert.ok(p.unmet_filters.length+p.unknown_filters.length>0);}
    else{assert.equal(alternative,false);assert.deepEqual(p.unmet_filters,[]);assert.deepEqual(p.unknown_filters,[]);}
   }
  }));
  await t.test("price zero is a real bound and not skipped",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],price:{max:0}},limit:100});assert.deepEqual(strictIds(r),[100007]);
   const d=r.results.find(p=>p.article_id===100000)!.unmet_filters[0]!;assert.equal(d.requested_value,0);assert.equal(d.actual_value,20000);
  });
  await t.test("boolean false is matched using boolean storage, not truthiness",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0093",op:"eq",value:false}]},limit:100});
   assert.equal(r.meta!.total_matches,9);assert.equal(r.results.find(p=>p.article_id===100005)!.unmet_filters[0]!.actual_value,true);
   assert.equal(r.results.find(p=>p.article_id===100008)!.unknown_filters[0]!.actual_value,null);
  });
  await t.test("missing numeric/NaN/wrong stored type are unknown, never zero",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]},limit:100});
   for(const id of [100003,100009,100010]){const hit=r.results.find(p=>p.article_id===id)!;assert.equal(hit.match_type,"alternative");assert.equal(hit.unknown_filters[0]!.actual_value,null);}
   assert.equal(r.meta!.total_matches,6);
  });
  await t.test("missing required feature evidence is unknown rather than false",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],feature_ids_all:["FT0023"]},limit:100});
   const hit=r.results.find(p=>p.article_id===100003)!;assert.equal(hit.unknown_filters[0]!.reason,"feature_not_recorded");assert.equal(hit.unknown_filters[0]!.actual_value,null);
  });
  await t.test("multiple feature constraints are AND; duplicate selectors preserve paths without double ranking penalty",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],feature_ids_all:["FT0023","FT0074"]},limit:100});assert.deepEqual(strictIds(r),[100000,100005,100006]);
   const duplicated=await run({exact:{article_id:100003},filters:{category_ids:["TV0108"],feature_queries_all:["HDMI"],feature_ids_all:["FT0023"]}});
   assert.equal(duplicated.results[0]!.unknown_filters.length,2);assert.equal(duplicated.results[0]!.difference_count,1);
  });
  await t.test("brand choices are OR and report a collection difference, not per-brand AND",async()=>{
   const both=await run({filters:{category_ids:["TV0108"],brand_queries:["LG","Bosch"]},limit:100});assert.equal(both.meta!.total_matches,11);
   const lg=await run({filters:{category_ids:["TV0108"],brand_queries:["LG"]},limit:100});
   assert.equal(lg.results.find(p=>p.article_id===100002)!.unmet_filters[0]!.path,"/filters/brand_queries");
  });
  await t.test("converted interval equality/inequalities use the declared precision epsilon",async()=>{
   for(const op of ["eq","gte","lte"] as const){
    const r=await run({exact:{article_id:100000},filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op,value:139.7,unit:"cm"}]}});
    assert.equal(r.results[0]!.match_type,"matches_applied_filters");
   }
  });
  await t.test("numeric mismatch diagnostics share canonical units and retain original input",async()=>{
   const r=await run({exact:{article_id:100000},filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0006",op:"eq",value:1200,unit:"mm"}]}});
   const d=r.results[0]!.unmet_filters[0]!;assert.equal(d.requested_value,120);assert.equal(d.actual_value,100);assert.equal(d.canonical_unit,"cm");
   assert.equal(d.input_value,1200);assert.equal(d.input_unit,"mm");assert.equal(d.actual_numeric_decimal,"100");
  });
  await t.test("text equality preserves case and numeric-looking text",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0014",op:"eq",value:"Wood"}]},limit:100});assert.equal(r.meta!.total_matches,10);
   const wrongCase=await run({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0014",op:"eq",value:"wood"}]},limit:100});assert.equal(wrongCase.meta!.total_matches,0);
  });
  await t.test("multiple/overlapping category groups merge globally with no duplicate Article",async()=>{
   const r=await run({filters:{category_ids:["TV0100","TV0108"]},limit:100});assert.equal(r.meta!.total_matches,12);assert.equal(new Set(idList(r)).size,12);
  });
  await t.test("an impossible ID branch is excluded rather than relaxed into irrelevant products",async()=>{
   const r=await run({filters:{category_ids:["TV0108","AP0101"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]},limit:100});
   assert.deepEqual(r.resolved_categories,["TV0108"]);assert.ok(!idList(r).includes(100012));
  });
  await t.test("unapplied legacy filter remains global and matches only claim applied filters",async()=>{
   const r=await run({filters:{category_query:"ทีวี OLED",specs:[{attribute_query:"unknownxyz",op:"eq",value:55}]}});
   assert.equal(r.meta!.all_requested_filters_applied,false);assert.equal(r.filter_resolution!.branches[0]!.unapplied_filters.length,1);
   assert.ok(r.results.every(p=>p.match_type==="matches_applied_filters" && p.unknown_filters.length===0));
  });
  await t.test("ambiguous metadata and contradictory constraints stop before product results",async()=>{
   const ambiguous=await run({filters:{category_ids:["TV0108"],specs:[{attribute_query:"ขนาด",op:"eq",value:55}]}});assert.equal(ambiguous.status,"needs_clarification");assert.deepEqual(ambiguous.results,[]);
   const conflict=await run({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"gte",value:65},{attribute_id:"AT0048",op:"lte",value:55}]}});assert.equal(conflict.status,"constraint_conflict");assert.deepEqual(conflict.results,[]);
  });
  for(const exact of [{article_id:100000},{model:"TV0108-001"}])await t.test(`explicit identity lock ${JSON.stringify(exact)}`,async()=>{
   const r=await run({exact,filters:{category_ids:["TV0108"],price:{max:1}},limit:100});assert.deepEqual(idList(r),[100000]);assert.equal(r.results[0]!.match_type,"alternative");
  });
  for(const query of ["100000","TV0108-001"])await t.test(`literal query identity lock ${query}`,async()=>{assert.deepEqual(idList(await run({query,limit:100})),[100000]);});
  for(const input of [{exact:{article_id:199999}},{exact:{model:"DOES-NOT-EXIST"}},{query:"199999"},{query:"TV0108-999"}])
   await t.test(`unknown identity never recommends a different model: ${JSON.stringify(input)}`,async()=>{const r=await run(input);assert.equal(r.status,"no_matches");assert.deepEqual(r.results,[]);assert.equal(r.meta!.total_alternatives,0);});
  for(const input of [{exact:{article_id:100000,model:"TV0108-002"}},{query:"100000",exact:{article_id:100001}},
   {query:"TV0108-001",exact:{model:"TV0108-002"}},{query:"100001",exact:{model:"TV0108-001"}}])
   await t.test(`contradictory identity selectors: ${JSON.stringify(input)}`,async()=>assert.equal((await run(input)).status,"constraint_conflict"));
  await t.test("drafts remain hidden through literal/exact IDs, models and keyword documents",async()=>reader(async()=>{
   for(const input of [{exact:{article_id:106045}},{exact:{model:"TV0108-900"}},{query:"106045"},{query:"TV0108-900"}])assert.equal((await run(input)).status,"no_matches");
   const words=await run({query:"DRAFTONLYSECRET",limit:100});assert.equal(words.meta!.total_matches,0);assert.ok(idList(words).every(id=>id<106045));
  }));
  await t.test("explicit published condition hides drafts even for owner bypassing RLS",async()=>assert.equal((await run({exact:{article_id:106045}})).status,"no_matches"));
  await t.test("strict tier precedes cheap alternatives under price sort",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]},sort:"price_asc",limit:100});
   const firstAlternative=r.results.findIndex(p=>p.match_type==="alternative");assert.equal(firstAlternative,6);assert.equal(r.results[firstAlternative]!.article_id,100007);
   assert.ok(strictIds(r).includes(100006));
  });
  await t.test("numeric alternatives prefer smaller normalized violations when other evidence ties",async()=>{
   const r=await run({filters:{category_ids:["TV0108"],price:{max:25000}},limit:100});
   const alternatives=r.results.filter(p=>p.match_type==="alternative");assert.deepEqual(alternatives.map(p=>p.article_id),[100009,100010,100001]);
   assert.ok(alternatives[0]!.numeric_distance<alternatives[2]!.numeric_distance);
  });
  await t.test("unknown prices stay null, including nonfinite stored numeric",async()=>{
   for(const price of [null,"NaN"]){
    await db.query("UPDATE products SET price=$1::numeric WHERE id=100000",[price]);
    try{const r=await run({exact:{article_id:100000},filters:{category_ids:["TV0108"],price:{max:25000}}});
     assert.equal(r.results[0]!.unknown_filters[0]!.actual_value,null);assert.equal(r.results[0]!.unknown_filters[0]!.reason,"price_unknown");
    }finally{await db.exec("UPDATE products SET price=20000 WHERE id=100000");}
   }
  });
  await t.test("a missing keyword document retains core title and category evidence",async()=>{
   const saved=(await db.query<{keyword_text:string}>("SELECT keyword_text FROM product_search_documents WHERE product_id=100000")).rows[0]!.keyword_text;
   await db.exec("DELETE FROM product_search_documents WHERE product_id=100000");
   try{assert.equal((await run({query:"ทีวี",exact:{article_id:100000}})).results[0]!.match_type,"matches_applied_filters");}
   finally{await db.query("INSERT INTO product_search_documents(product_id,keyword_text) VALUES(100000,$1)",[saved]);}
  });
  await t.test("stable paging follows global tier/rank; changing limit does not change candidate pools",async()=>{
   const request:ToolSearchRequest={filters:{category_ids:["TV0108"],specs:[{attribute_id:"AT0048",op:"eq",value:55}]},sort:"relevance",limit:100};
   const all=await run(request);const paged:number[]=[];
   for(let page=1;page<=6;page++){const r=await run({...request,limit:2,page});paged.push(...idList(r));assert.equal(r.meta!.total_matches,all.meta!.total_matches);}
   assert.deepEqual(paged,idList(all));assert.equal(new Set(paged).size,paged.length);
  });
  await t.test("tied score sorts by Article deterministically",async()=>{
   assert.deepEqual(idList(await run({sort:"id",limit:100})),[100000,100001,100002,100003,100004,100005,100006,100007,100008,100009,100010]);
   assert.deepEqual(await run({query:"ทีวี"}),await run({query:"ทีวี"}));
  });
  await t.test("exhausted page is ok with known totals, not a false global no_matches",async()=>{
   const r=await run({page:10001,limit:1});assert.equal(r.status,"ok");assert.deepEqual(r.results,[]);assert.equal(r.meta!.has_more,false);assert.equal(r.meta!.total_matches,11);
  });
  await t.test("fewer candidates than limit returns only available products",async()=>{const r=await run({limit:100});assert.equal(r.meta!.returned_count,11);assert.equal(r.meta!.has_more,false);});
  await t.test("semantic-only without usable keyword constraints asks for clarification",async()=>{
   const r=await raw({semantic_query:"ทีวีสำหรับดูหนัง"});assert.equal(r.status,"needs_clarification");assert.equal(r.issues![0]!.path,"/semantic_query");assert.deepEqual(r.results,[]);
   const usable=await run({semantic_query:"ทีวีสำหรับดูหนัง"});assert.equal(usable.semantic_available,false);assert.equal(usable.meta!.total_matches,11);
  });
  const invalid=[null,[],{}, {query:" "},{filters:{price:{currency:"THB"}}},{filters:{category_ids:["TV0108"]},page:0},
   {query:1},{query:"a".repeat(201)},{semantic_query:null},{version:"2"},{page:"1"},{limit:0},{limit:101},{page:10001,limit:2},{sort:"sql"},
   {exact:{}},{exact:{article_id:"100000"}},{exact:{article_id:99999}},{exact:{model:null}},{exact:{sku:"x"}},
   {filters:{category_ids:[]}},{filters:{resolved_specs:[]}},{results:[]},{filters:{price:{min:10,max:0}}}];
  for(const [i,input]of invalid.entries())await t.test(`SQL validates raw request ${i}`,async()=>{
   await assert.rejects(raw(input),(e:unknown)=>typeof e==="object"&&e!==null&&"code"in e&&e.code==="22023");
  });
  await t.test("reader cannot write; anon cannot call; existing search v1 is unchanged",async()=>{
   await reader(async()=>{await assert.rejects(db.exec("UPDATE products SET price=price"));assert.equal((await db.query("SELECT * FROM public.search_products('TV0108-001')")).rows.length,1);});
   await db.exec("SET ROLE anon");try{await assert.rejects(raw({filters:{category_ids:["TV0108"]}}));}finally{await db.exec("RESET ROLE");}
  });
  await t.test("migration down/reapply preserves all products, old helpers and public search",async()=>{
   assert.deepEqual(await applyMigrations(client,directory,"down"),["0006_keyword_product_search"]);
   assert.equal((await db.query<{n:number}>("SELECT count(*)::integer n FROM products")).rows[0]!.n,16);
   assert.equal((await db.query("SELECT * FROM public.search_products('TV0108-001')")).rows.length,1);
   assert.deepEqual(await applyMigrations(client,directory,"up"),["0006_keyword_product_search"]);assert.equal((await run()).status,"ok");
  });
 }finally{await db.close();await rm(directory,{recursive:true,force:true});}
});

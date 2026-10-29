import type {ProductHybridEngine} from "../services/product-hybrid.service.js";
import type {CategorySemanticEngine} from "../services/category-semantic.service.js";
import {resolveCategoryFilters} from "../services/category-selector.service.js";
import {parseToolSearchRequest} from "../validation/tool-search.validation.js";
import {CatalogInputError,CatalogDataError,type CatalogPool,type CatalogClient,type CatalogSearchSnapshot} from "../types/catalog-read.types.js";
import type {KeywordSearchEnvelope} from "../types/keyword-search.types.js";
import {assertArticle,createProductFactsRepository} from "./product-facts.repository.js";

/** One checked-out client, one snapshot; the caller owns pool creation, identity check and shutdown. */
export function createPostgresCatalogRepository(pool: CatalogPool,options:{categorySemantic?:CategorySemanticEngine|null;productHybrid?:ProductHybridEngine|null}={}) {
  async function read<T>(action:(client:CatalogClient)=>Promise<T>):Promise<T> {
    const client=await pool.connect();let destroy=false;
    try {
      await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
      const value=await action(client);
      await client.query("COMMIT");return value;
    } catch(error) {
      try { await client.query("ROLLBACK"); } catch { destroy=true; }
      throw error;
    } finally { client.release(destroy); }
  }
  return {
    async search(input:unknown):Promise<CatalogSearchSnapshot> {
      const parsed=parseToolSearchRequest(input);if(!parsed.valid)throw new CatalogInputError(parsed.issues);
      const semantic=options.categorySemantic;
      const probe=semantic && !parsed.value.filters.category_ids ? await read(async client=>{
        const lexical=await resolveCategoryFilters(client,parsed.value.filters);
        return semantic.hasWork(lexical)?{lexical,state:await semantic.readiness(client)}:null;
      }):null;
      // Release the snapshot/connection BEFORE any model HTTP request, then revalidate in the final snapshot.
      const prepared=probe && semantic?await semantic.prepare(probe.lexical,probe.state):null;
      const hybrid=options.productHybrid;
      const hybridProbe=hybrid?.requested(parsed.value)?await read(async client=>{
        let scope=await resolveCategoryFilters(client,parsed.value.filters);
        if(prepared && semantic)scope=await semantic.resolve(client,scope,prepared);
        if(scope.status!=="resolved")return {scope,state:null};
        return {scope,state:await hybrid.readiness(client,scope.category_ids,parsed.value)};
      }):null;
      if(hybridProbe && !hybridProbe.state)return {request:parsed.value,category_resolution:hybridProbe.scope,search:null,products:[]};
      const productPrepared=hybridProbe?.state && hybrid?await hybrid.prepare(hybridProbe.scope.category_ids,parsed.value,hybridProbe.state):null;
      return read(async client=>{
        let category_resolution=await resolveCategoryFilters(client,parsed.value.filters);
        if(prepared && semantic)category_resolution=await semantic.resolve(client,category_resolution,prepared);
        if(category_resolution.status!=="resolved")return {request:parsed.value,category_resolution,search:null,products:[]};
        const search=hybrid && productPrepared?await hybrid.search(client,category_resolution.category_ids,parsed.value,productPrepared):(await client.query<{result:KeywordSearchEnvelope}>(
          "SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb) AS result",
          [category_resolution.category_ids,JSON.stringify(parsed.value)])).rows[0]?.result;
        if(!search || (search.semantic_available && search.engine_version!=="hybrid-v1") || !Array.isArray(search.results) || search.results.length>parsed.value.limit)throw new CatalogDataError();
        const ids=search.results.map(hit=>hit.article_id);
        if(new Set(ids).size!==ids.length)throw new CatalogDataError();
        const products=await createProductFactsRepository(client).findMany(ids);
        // Same snapshot: a missing published fact is corruption, not permission to drop/reorder results.
        if(products.length!==ids.length || products.some((p,index)=>p.article_id!==ids[index] ||
          p.category_id!==search.results[index]!.category_id || p.is_mock!==search.results[index]!.is_mock))throw new CatalogDataError();
        return {request:parsed.value,category_resolution,search,products};
      });
    },
    async findDetails(article: number) {
      assertArticle(article);return read(client=>createProductFactsRepository(client).findDetails(article));
    },
  };
}

import type { PoolClient } from "pg";
import type { CategoryResolution } from "../types/category-resolution.types.js";
import type { ToolSearchFilters } from "../types/tool-search.types.js";
import type { TypedFilterResolution } from "../types/typed-filter.types.js";
import { parseToolSearchRequest } from "../validation/tool-search.validation.js";

/** Accept the server's resolved scope, never category IDs provided by a Tool caller. */
export function createTypedFilterRepository(client: Pick<PoolClient,"query">) {
  return {
    async resolve(scope: CategoryResolution, filters: ToolSearchFilters): Promise<TypedFilterResolution> {
      if (scope.status!=="resolved" || !scope.category_ids.length || scope.category_ids.length>1907 ||
        scope.category_ids.some(id=>!id || !/^[A-Z]{2}[0-9]{4}$/.test(id)))
        throw new TypeError("Resolve a valid category scope before filters");
      const parsed=parseToolSearchRequest({query:"metadata resolution",filters});
      if (!parsed.valid) throw new TypeError("Invalid typed filter request");
      // Category names have already been resolved in Commit 12. Guard against mismatched orchestration.
      if (parsed.value.filters.category_query!==undefined && parsed.value.filters.category_query!==scope.query)
        throw new TypeError("Category query differs from resolved scope");
      const result=await client.query<{resolution: TypedFilterResolution}>(
        "SELECT catalog_search.resolve_typed_filters($1::text[],$2::jsonb) AS resolution",
        [scope.category_ids,JSON.stringify(parsed.value.filters)]);
      if (!result.rows[0]) throw new Error("Typed filter resolution unavailable");
      return result.rows[0].resolution;
    },
  };
}

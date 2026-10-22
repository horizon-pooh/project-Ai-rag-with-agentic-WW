import type { PoolClient } from "pg";
import type { AttributeLabelCandidate, CategoryAttribute, CategoryLabelCandidate,
  MetadataAudit, UnitConversion } from "../types/metadata.types.js";

type QueryClient = Pick<PoolClient, "query">;
const attributeColumns = `category_id,attribute_id,code,name_th,aliases,data_type,
  unit_code,unit_label,is_required,is_filterable,min_value::text,max_value::text,display_order`;
function label(value: string): void {
  if (typeof value !== "string" || !value.trim() || [...value].length > 200)
    throw new TypeError("Metadata query must contain 1–200 characters");
}
function category(value: string): void {
  if (!/^[A-Z]{2}[0-9]{4}$/.test(value)) throw new TypeError("Invalid category ID");
}
export function createMetadataRepository(client: QueryClient) {
  return {
    async version(): Promise<string> {
      const result = await client.query<{version: string}>("SELECT catalog_search.metadata_version() AS version");
      return result.rows[0]!.version;
    },
    async findCategoryLabels(query: string): Promise<CategoryLabelCandidate[]> {
      label(query);
      return (await client.query<CategoryLabelCandidate>(
        "SELECT * FROM catalog_search.category_label_matches($1::text)", [query])).rows;
    },
    async findAttributeLabels(categoryId: string, query: string): Promise<AttributeLabelCandidate[]> {
      category(categoryId); label(query);
      return (await client.query<AttributeLabelCandidate>(`SELECT ${attributeColumns},match_source
        FROM catalog_search.attribute_label_matches($1::text,$2::text)`, [categoryId,query])).rows;
    },
    async listCategoryAttributes(categoryId: string): Promise<CategoryAttribute[]> {
      category(categoryId);
      return (await client.query<CategoryAttribute>(`SELECT ${attributeColumns}
        FROM public.category_attribute_search_catalog WHERE category_id=$1::text
        ORDER BY display_order,attribute_id`, [categoryId])).rows;
    },
    async convertUnit(value: number, from: string | null, to: string | null): Promise<UnitConversion> {
      if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError("Expected finite numeric value");
      for (const unit of [from,to]) {
        if (unit !== null && (typeof unit !== "string" || [...unit.trim()].length > 32))
          throw new TypeError("Invalid unit label");
      }
      const result = await client.query<UnitConversion>(`SELECT status,value_out::text,from_unit,to_unit,dimension,rule_version
        FROM catalog_search.convert_unit($1::numeric,$2::text,$3::text)`, [value,from,to]);
      return result.rows[0]!;
    },
    async audit(): Promise<MetadataAudit> {
      const result = await client.query<{audit: MetadataAudit}>("SELECT catalog_search.metadata_audit() AS audit");
      return result.rows[0]!.audit;
    },
  };
}

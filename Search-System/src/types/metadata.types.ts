export interface CategoryLabelCandidate {
  category_id: string;
  parent_id: string | null;
  level: number;
  name_th: string;
  name_en: string | null;
  aliases: string[];
  match_source: "id" | "name" | "alias";
}
export interface CategoryAttribute {
  category_id: string;
  attribute_id: string;
  code: string;
  name_th: string;
  aliases: string[];
  data_type: string;
  unit_code: string | null;
  unit_label: string | null;
  is_required: boolean;
  is_filterable: boolean;
  // PostgreSQL numeric is preserved as decimal text; never parseFloat implicitly.
  min_value: string | null;
  max_value: string | null;
  display_order: number;
}
export interface AttributeLabelCandidate extends CategoryAttribute {
  match_source: "id" | "code" | "name" | "alias";
}
export interface UnitConversion {
  status: "converted" | "unchanged" | "missing_unit" | "unsupported_unit" | "disabled_rule";
  value_out: string | null;
  from_unit: string | null;
  to_unit: string | null;
  dimension: string | null;
  rule_version: string | null;
}
export interface MetadataAudit {
  version: string;
  categories: number;
  attributes: number;
  category_attributes: number;
  enabled_unit_rules: number;
  categories_without_aliases: number;
  attributes_without_aliases: number;
  category_collision_count: number;
  attribute_collision_count: number;
  category_collision_examples: {label: string; ids: string[]}[];
  attribute_collision_examples: {category_id: string; label: string; ids: string[]}[];
  unit_codes_without_rules: string[];
}

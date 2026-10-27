import type {ProductFacts, FactValue} from "../types/product-facts.types.js";
import {CatalogDataError, type CatalogClient, type CatalogProductDetails,
  type CatalogContentSection} from "../types/catalog-read.types.js";

export function assertArticle(value: unknown): asserts value is number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 100000 || value > 199999)
    throw new TypeError("Expected integer Article 100000–199999");
}
/** Decimal strings remain available in details; JSON facts use finite JS numbers only. */
export function decimalFact(value: string | null): number | null {
  if (value === null || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)) return null;
  const number = Number(value);
  if (!Number.isFinite(number) || (Number.isInteger(number) && !Number.isSafeInteger(number))) return null;
  if (number === 0 && /[1-9]/.test(value.split(/[eE]/)[0]!)) return null;
  return number;
}
interface ProductRow {
  article_id: number; name_th: string; model: string; brand_id: string; brand_name: string;
  category_id: string; category_name: string; price_decimal: string; currency: string;
  is_mock: boolean; mock_notice: string | null; name_en: string | null; series: string | null;
  short_description: string; regular_price_decimal: string | null; sale_unit: string;
  pack_quantity: number; stock_quantity: number;
}
interface SpecRow {
  product_id: number; attribute_id: string; name: string; data_type: "number" | "text" | "boolean";
  stored_type: string | null; numeric_decimal: string | null; value_text: string | null;
  value_boolean: boolean | null; unit: string | null; is_required: boolean;
}
interface FeatureRow {
  product_id: number; feature_id: string; name: string; detail: string; benefit: string; limitation: string;
}
const productSql = `
 SELECT p.id AS article_id,p.name_th,p.model,p.brand_id,coalesce(nullif(b.name_th,''),b.name) AS brand_name,
 p.category_id,c.name_th AS category_name,p.price::text AS price_decimal,p.currency,p.is_mock,p.mock_notice,
 p.name_en,p.series,p.short_description,p.regular_price::text AS regular_price_decimal,
 p.sale_unit,p.pack_quantity,p.stock_quantity
 FROM public.products p JOIN public.brands b ON b.id=p.brand_id JOIN public.categories c ON c.id=p.category_id
 WHERE p.id=ANY($1::integer[]) AND p.status='published' ORDER BY p.id`;
// One batch across all selected Articles; union includes category definitions with missing values
// and recorded non-filterable specs. No specs x features x sections cross product.
const specsSql = `
 WITH selected AS MATERIALIZED (SELECT id,category_id FROM public.products WHERE id=ANY($1::integer[]) AND status='published'),
 keys AS (
  SELECT p.id AS product_id,p.category_id,ca.attribute_id FROM selected p JOIN public.category_attributes ca ON ca.category_id=p.category_id
  UNION
  SELECT p.id,p.category_id,v.attribute_id FROM selected p JOIN public.product_attribute_values v ON v.product_id=p.id AND v.category_id=p.category_id
 )
 SELECT k.product_id,a.id AS attribute_id,a.name_th AS name,a.data_type,v.data_type AS stored_type,
 v.value_number::text AS numeric_decimal,v.value_text,v.value_boolean,
 CASE WHEN a.data_type='number' THEN coalesce(nullif(a.unit_code,''),nullif(a.unit_label,'')) ELSE NULL END AS unit,
 coalesce(ca.is_required,false) AS is_required
 FROM keys k JOIN public.attribute_definitions a ON a.id=k.attribute_id
 LEFT JOIN public.category_attributes ca ON ca.category_id=k.category_id AND ca.attribute_id=k.attribute_id
 LEFT JOIN public.product_attribute_values v ON v.product_id=k.product_id AND v.category_id=k.category_id AND v.attribute_id=k.attribute_id
 ORDER BY k.product_id,ca.display_order NULLS LAST,a.id`;
const featuresSql = `
 SELECT pf.product_id,pf.feature_id,f.name_th AS name,pf.detail,pf.benefit,pf.limitation
 FROM public.product_features pf JOIN public.products p ON p.id=pf.product_id AND p.category_id=pf.category_id
 JOIN public.features f ON f.id=pf.feature_id
 WHERE p.id=ANY($1::integer[]) AND p.status='published'
 ORDER BY p.id,pf.display_order,pf.feature_id`;
function specValue(row: SpecRow): FactValue | null {
  if (row.stored_type !== row.data_type) return null;
  switch (row.data_type) {
    case "number": return decimalFact(row.numeric_decimal);
    case "boolean": return row.value_boolean;
    case "text": return row.value_text;
  }
}
function facts(row: ProductRow, specifications: ProductFacts["specifications"], features: ProductFacts["features"]): ProductFacts {
  const price=decimalFact(row.price_decimal);
  if (price === null || price < 0 || row.currency !== "THB" || !row.name_th?.trim() || !row.model?.trim() ||
      !row.brand_name?.trim() || !row.category_name?.trim() || (row.is_mock && !row.mock_notice?.trim())) throw new CatalogDataError();
  return {article_id: row.article_id,name_th: row.name_th,model: row.model,brand_id: row.brand_id,brand_name: row.brand_name,
    category_id: row.category_id,category_name: row.category_name,price,currency: "THB",is_mock: row.is_mock,
    mock_notice: row.mock_notice,specifications,features};
}
export function createProductFactsRepository(client: CatalogClient) {
  const load = async (input: readonly number[], details: boolean): Promise<CatalogProductDetails[]> => {
    if (!Array.isArray(input) || input.length > 100) throw new TypeError("Expected at most 100 Articles");
    input.forEach(assertArticle);
    const ids=[...new Set(input)]; if (!ids.length) return [];
    const rows=(await client.query<ProductRow>(productSql,[ids])).rows;
    if (!rows.length) return [];
    const visible=rows.map(r=>r.article_id);
    const specs=(await client.query<SpecRow>(specsSql,[visible])).rows;
    const features=(await client.query<FeatureRow>(featuresSql,[visible])).rows;
    const sections=details ? (await client.query<CatalogContentSection & {product_id:number}>(`
      SELECT s.product_id,s.section_type,s.title,s.applicability,s.items,s.display_order
      FROM public.product_content_sections s JOIN public.products p ON p.id=s.product_id
      WHERE p.id=ANY($1::integer[]) AND p.status='published' ORDER BY p.id,s.display_order,s.section_type`,[visible])).rows : [];
    const specGroups=new Map<number,SpecRow[]>(), featureGroups=new Map<number,FeatureRow[]>(), sectionGroups=new Map<number,CatalogContentSection[]>();
    for (const s of specs) { const g=specGroups.get(s.product_id)??[];g.push(s);specGroups.set(s.product_id,g); }
    for (const f of features) { const g=featureGroups.get(f.product_id)??[];g.push(f);featureGroups.set(f.product_id,g); }
    for (const {product_id,...s} of sections) { const g=sectionGroups.get(product_id)??[];g.push(s);sectionGroups.set(product_id,g); }
    const byId=new Map(rows.map(row=>{
      const specifications=(specGroups.get(row.article_id)??[]).map(s=>({attribute_id:s.attribute_id,name:s.name,
        value:specValue(s),unit:s.unit,data_type:s.data_type,numeric_decimal:s.data_type==="number" && s.stored_type===s.data_type?s.numeric_decimal:null,is_required:s.is_required}));
      const feature_details=(featureGroups.get(row.article_id)??[]).map(({product_id: _id,...f})=>f);
      const product=facts(row,specifications.map(({attribute_id,name,value,unit})=>({attribute_id,name,value,unit})),
        feature_details.map(({feature_id,name})=>({feature_id,name})));
      return [row.article_id,{product,name_en:row.name_en,series:row.series,short_description:row.short_description,
        regular_price:decimalFact(row.regular_price_decimal),sale_unit:row.sale_unit,pack_quantity:row.pack_quantity,
        stock_quantity:row.stock_quantity,specifications,feature_details,content_sections:sectionGroups.get(row.article_id)??[]}];
    }));
    return ids.flatMap(id=>byId.has(id)?[byId.get(id)!]:[]);
  };
  return {
    async findMany(ids: readonly number[]): Promise<ProductFacts[]> { return (await load(ids,false)).map(r=>r.product); },
    async findDetails(article: number): Promise<CatalogProductDetails | null> { assertArticle(article);return (await load([article],true))[0]??null; },
  };
}

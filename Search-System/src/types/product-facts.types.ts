/** Public facts use database Article integers; legacy mock Product stays unchanged. */
export type FactValue = string | number | boolean;

export interface ProductFacts {
  article_id: number;
  name_th: string;
  model: string;
  brand_id: string;
  brand_name: string;
  category_id: string;
  category_name: string;
  price: number;
  currency: "THB";
  is_mock: boolean;
  mock_notice: string | null;
  specifications: Array<{
    attribute_id: string;
    name: string;
    value: FactValue | null;
    unit: string | null;
  }>;
  features: Array<{ feature_id: string; name: string }>;
}

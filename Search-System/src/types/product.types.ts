export type ProductAttributeValue =
  | string
  | number
  | boolean;

export interface Product {
  id: string;

  name: string;
  description?: string;

  brand?: string;
  category?: string;

  tags: string[];
  aliases: string[];

  attributes: Record<
    string,
    ProductAttributeValue
  >;

  searchableText?: string;

  createdAt?: Date;
  updatedAt?: Date;
}
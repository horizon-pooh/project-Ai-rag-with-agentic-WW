import type { Product } from "../types/product.types.js";

export interface ProductRepository {
  findAll(): Promise<Product[]>;

  findById(
    id: string
  ): Promise<Product | null>;
}
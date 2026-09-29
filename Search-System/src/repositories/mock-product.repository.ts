import { mockProducts } from "../data/mock/products.js";

import type { Product } from "../types/product.types.js";
import type { ProductRepository } from "./product.repository.js";

export class MockProductRepository
  implements ProductRepository
{
  private readonly products: Product[];

  constructor(
    products: Product[] = mockProducts
  ) {
    this.products = products;
  }

  async findAll(): Promise<Product[]> {
    return [...this.products];
  }

  async findById(
    id: string
  ): Promise<Product | null> {
    const product = this.products.find(
      (item) => item.id === id
    );

    return product ?? null;
  }
}
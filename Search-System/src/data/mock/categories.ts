export const mockCategories = [
  "Keyboard",
  "Mouse",
  "Headphones",
  "Monitor",
  "Laptop",
  "Webcam",
  "Storage",
  "Accessories",
] as const;

export type MockCategory =
  (typeof mockCategories)[number];
import type { Product } from "../../types/product.types.js";

export const mockProducts: Product[] = [
  {
    id: "P001",
    name: "Wireless Mechanical Keyboard",
    description:
      "Compact wireless mechanical keyboard with Bluetooth connectivity.",
    brand: "KeyNova",
    category: "Keyboard",
    tags: [
      "wireless",
      "mechanical",
      "bluetooth",
      "keyboard",
    ],
    aliases: [
      "wireless keyboard",
      "mechanical keyboard",
      "bluetooth keyboard",
    ],
    attributes: {
      connection: "Bluetooth",
      layout: "75%",
      switchType: "Mechanical",
      color: "Black",
    },
    searchableText:
      "wireless mechanical keyboard bluetooth compact 75 percent",
  },

  {
    id: "P002",
    name: "Wired Mechanical Keyboard",
    description:
      "Full-size mechanical keyboard designed for desktop and gaming use.",
    brand: "KeyNova",
    category: "Keyboard",
    tags: [
      "wired",
      "mechanical",
      "gaming",
      "keyboard",
    ],
    aliases: [
      "wired keyboard",
      "gaming keyboard",
    ],
    attributes: {
      connection: "USB",
      layout: "100%",
      switchType: "Mechanical",
      color: "Black",
    },
    searchableText:
      "wired mechanical gaming keyboard usb full size",
  },

  {
    id: "P003",
    name: "Slim Bluetooth Keyboard",
    description:
      "Portable slim keyboard suitable for tablets and laptops.",
    brand: "TypeFlow",
    category: "Keyboard",
    tags: [
      "bluetooth",
      "slim",
      "portable",
      "keyboard",
    ],
    aliases: [
      "portable keyboard",
      "tablet keyboard",
    ],
    attributes: {
      connection: "Bluetooth",
      layout: "Compact",
      color: "White",
    },
    searchableText:
      "slim bluetooth portable keyboard tablet laptop",
  },

  {
    id: "P004",
    name: "Wireless Gaming Mouse",
    description:
      "Lightweight wireless gaming mouse with adjustable DPI.",
    brand: "VoltGear",
    category: "Mouse",
    tags: [
      "wireless",
      "gaming",
      "mouse",
      "lightweight",
    ],
    aliases: [
      "gaming mouse",
      "wireless mouse",
    ],
    attributes: {
      connection: "Wireless",
      dpi: 26000,
      color: "Black",
    },
    searchableText:
      "wireless gaming mouse lightweight adjustable dpi",
  },

  {
    id: "P005",
    name: "Ergonomic Wireless Mouse",
    description:
      "Comfort-focused wireless mouse designed for office productivity.",
    brand: "WorkEase",
    category: "Mouse",
    tags: [
      "wireless",
      "ergonomic",
      "office",
      "mouse",
    ],
    aliases: [
      "office mouse",
      "ergonomic mouse",
    ],
    attributes: {
      connection: "Wireless",
      dpi: 4000,
      ergonomic: true,
    },
    searchableText:
      "ergonomic wireless office mouse productivity comfortable",
  },

  {
    id: "P006",
    name: "USB Optical Mouse",
    description:
      "Simple wired optical mouse for everyday computer use.",
    brand: "CoreLink",
    category: "Mouse",
    tags: [
      "wired",
      "usb",
      "optical",
      "mouse",
    ],
    aliases: [
      "wired mouse",
      "usb mouse",
    ],
    attributes: {
      connection: "USB",
      dpi: 1600,
      color: "Black",
    },
    searchableText:
      "usb wired optical mouse everyday computer",
  },

  {
    id: "P007",
    name: "Wireless Noise Cancelling Headphones",
    description:
      "Over-ear wireless headphones with active noise cancellation.",
    brand: "SoundPeak",
    category: "Headphones",
    tags: [
      "wireless",
      "headphones",
      "noise cancelling",
      "bluetooth",
    ],
    aliases: [
      "noise cancelling headphones",
      "bluetooth headphones",
    ],
    attributes: {
      connection: "Bluetooth",
      noiseCancelling: true,
      batteryHours: 40,
    },
    searchableText:
      "wireless bluetooth noise cancelling headphones over ear",
  },

  {
    id: "P008",
    name: "Gaming Headset Pro",
    description:
      "Gaming headset with detachable microphone and surround audio.",
    brand: "VoltGear",
    category: "Headphones",
    tags: [
      "gaming",
      "headset",
      "microphone",
      "surround",
    ],
    aliases: [
      "gaming headphones",
      "gaming headset",
    ],
    attributes: {
      connection: "USB",
      microphone: true,
      surroundSound: true,
    },
    searchableText:
      "gaming headset headphones microphone surround sound usb",
  },

  {
    id: "P009",
    name: "27 Inch Gaming Monitor",
    description:
      "High refresh rate gaming monitor with QHD resolution.",
    brand: "PixelForge",
    category: "Monitor",
    tags: [
      "gaming",
      "monitor",
      "qhd",
      "high refresh rate",
    ],
    aliases: [
      "gaming display",
      "27 inch monitor",
    ],
    attributes: {
      screenSize: 27,
      resolution: "2560x1440",
      refreshRate: 165,
    },
    searchableText:
      "27 inch gaming monitor qhd 165hz high refresh rate",
  },

  {
    id: "P010",
    name: "24 Inch Office Monitor",
    description:
      "Full HD monitor designed for productivity and office work.",
    brand: "ViewCore",
    category: "Monitor",
    tags: [
      "office",
      "monitor",
      "full hd",
      "productivity",
    ],
    aliases: [
      "office display",
      "24 inch monitor",
    ],
    attributes: {
      screenSize: 24,
      resolution: "1920x1080",
      refreshRate: 75,
    },
    searchableText:
      "24 inch office monitor full hd productivity display",
  },

  {
    id: "P011",
    name: "Ultralight Student Laptop",
    description:
      "Portable laptop designed for studying and everyday productivity.",
    brand: "NovaBook",
    category: "Laptop",
    tags: [
      "laptop",
      "student",
      "portable",
      "lightweight",
    ],
    aliases: [
      "student notebook",
      "portable laptop",
    ],
    attributes: {
      memoryGB: 16,
      storageGB: 512,
      screenSize: 14,
    },
    searchableText:
      "lightweight student laptop portable notebook study productivity",
  },

  {
    id: "P012",
    name: "Performance Gaming Laptop",
    description:
      "High-performance laptop designed for gaming and creative workloads.",
    brand: "NovaBook",
    category: "Laptop",
    tags: [
      "gaming",
      "laptop",
      "performance",
      "graphics",
    ],
    aliases: [
      "gaming notebook",
      "performance laptop",
    ],
    attributes: {
      memoryGB: 32,
      storageGB: 1000,
      screenSize: 16,
    },
    searchableText:
      "performance gaming laptop notebook graphics creative workload",
  },

  {
    id: "P013",
    name: "Full HD USB Webcam",
    description:
      "1080p webcam suitable for meetings, streaming, and online classes.",
    brand: "VisionGo",
    category: "Webcam",
    tags: [
      "webcam",
      "usb",
      "1080p",
      "streaming",
    ],
    aliases: [
      "computer camera",
      "streaming camera",
    ],
    attributes: {
      resolution: "1920x1080",
      connection: "USB",
      microphone: true,
    },
    searchableText:
      "full hd usb webcam 1080p streaming meeting online class camera",
  },

  {
    id: "P014",
    name: "Portable External SSD",
    description:
      "Compact high-speed external solid state drive for portable storage.",
    brand: "DataFlash",
    category: "Storage",
    tags: [
      "ssd",
      "storage",
      "portable",
      "external",
    ],
    aliases: [
      "external drive",
      "portable ssd",
    ],
    attributes: {
      capacityGB: 1000,
      connection: "USB-C",
      portable: true,
    },
    searchableText:
      "portable external ssd solid state drive 1tb usb c storage",
  },

  {
    id: "P015",
    name: "USB-C Multiport Hub",
    description:
      "Multiport adapter with HDMI, USB, and memory card connectivity.",
    brand: "ConnectPro",
    category: "Accessories",
    tags: [
      "usb-c",
      "hub",
      "adapter",
      "hdmi",
    ],
    aliases: [
      "usb c adapter",
      "multiport adapter",
    ],
    attributes: {
      connection: "USB-C",
      hdmi: true,
      usbPorts: 3,
    },
    searchableText:
      "usb c multiport hub adapter hdmi usb memory card",
  },

  {
    id: "P016",
    name: "Laptop Cooling Stand",
    description:
      "Adjustable cooling stand designed for laptops and notebooks.",
    brand: "CoolBase",
    category: "Accessories",
    tags: [
      "laptop",
      "stand",
      "cooling",
      "accessory",
    ],
    aliases: [
      "laptop stand",
      "notebook cooler",
    ],
    attributes: {
      adjustable: true,
      coolingFans: 2,
    },
    searchableText:
      "laptop cooling stand notebook cooler adjustable accessory",
  },
];
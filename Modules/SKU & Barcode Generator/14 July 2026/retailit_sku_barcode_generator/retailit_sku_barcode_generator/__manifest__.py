# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
{
    "name": "Retail IT SKU & Barcode Generator",
    "author": "Retail IT",
    "maintainer": "Retail IT",
    "website": "",
    "version": "19.0.1.0.0",
    "category": "Inventory",
    "summary": "Auto-generate product barcodes and category-based SKUs (internal references).",
    "description": """
Retail IT SKU & Barcode Generator
==================================
Combines automatic product barcode generation with category-based SKU
(internal reference) generation into a single module:

- Auto-generate EAN/Code barcodes for products (single or mass action),
  with a barcode image rendered on the product form.
- Auto-generate barcodes on product creation (optional, per company).
- Category-based internal reference (SKU) generation using per-category
  sequences, with configurable numeric padding.
""",
    "depends": [
        "product",
        "stock",
        "base_setup",
        "sale_management",
    ],
    "external_dependencies": {
        "python": ["barcode"],
    },
    "data": [
        "security/retailit_sku_barcode_generator_groups.xml",
        "security/ir.model.access.csv",
        "views/generate_product_barcode_views.xml",
        "views/product_views.xml",
        "views/product_category_views.xml",
        "views/res_config_settings_views.xml",
        "views/server_actions_sku.xml",
    ],
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": True,
    "auto_install": False,
    "license": "Other proprietary",
}

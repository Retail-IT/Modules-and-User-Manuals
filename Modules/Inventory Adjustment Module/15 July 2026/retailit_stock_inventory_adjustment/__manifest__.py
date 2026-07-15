{
    "name": "Retail IT - Stock Inventory Adjustment",
    "version": "19.0.1.0.0",
    "category": "Retail IT/Warehouse",
    "summary": """
        Update stock via Inventory Adjustment Screen, inventory adjustment, stock inventory adjustment, inventory count, stock count,
        stocktaking, physical inventory,update quantity on hand,counted quantity,inventory by location,inventory by warehouse,
        inventory by category,inventory import,import stock inventory, import inventory adjustment, import inventory from Excel,
        import inventory from CSV,bulk inventory import,mass inventory update,opening stock import
    """,
    "description": """
Retail IT - Stock Inventory Adjustment
=======================================
Perform stock inventory adjustments and counts (cycle or full count) with
barcode scanning, Excel/CSV import, Beyond ID RFID cloud import, and export
to PDF, Excel, or Google Sheets.

This module is proprietary to Retail IT. All rights reserved — see LICENSE
for full terms. It is licensed for use only by the client(s) for whom it was
deployed and may not be redistributed, sublicensed, or reused on other
installations without Retail IT's prior written consent.
""",
    "author": "Retail IT",
    "maintainer": "Retail IT",
    "license": "Other proprietary",
    "depends": [
        "base",
        "stock",
        "barcodes",
        "stock_account",
        "retailit_beyondid_manager",
    ],
    "external_dependencies": {"python": ["openpyxl", "xlsxwriter"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/retailit_stock_inventory_views.xml",
        "views/retailit_stock_inventory_cloud_import_views.xml",
        "report/report.xml",
        "report/inventory_report_template.xml",
    ],
    "images": ["static/description/banner_grid.png"],
    "assets": {
        "web.assets_backend": [
            "retailit_stock_inventory_adjustment/static/src/scanner/**/*",
            "retailit_stock_inventory_adjustment/static/src/cloud_import/**/*",
        ],
    },
    "installable": True,
    "auto_install": False,
}

{
    "name": "Retail IT - Beyond ID Product Sync",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "Synchronize Odoo product variants to Beyond ID.",
    "description": """
Beyond ID Product Sync (Retail IT edition)
===========================================

Synchronizes Odoo product variants to Beyond ID.

This module is proprietary software. See the LICENSE file included in this
module's root folder for the full terms of use.
""",
    "author": "Retail IT",
    "maintainer": "Retail IT",
    "license": "Other proprietary",
    "depends": [
        "retailit_beyondid_manager",
        "product",
        "stock",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/product_sync_cleanup.xml",
        "views/res_config_settings_views.xml",
        "views/beyondid_product_sync_views.xml",
        "data/ir_cron.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "retailit_beyondid_product_sync/static/src/product_sync_progress/**/*",
            "retailit_beyondid_product_sync/static/src/initial_load_progress/**/*",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}

{
    "name": "Retail IT - Beyond ID Manager",
    "version": "19.0.1.0.0",
    "category": "Retail IT/Integrations",
    "summary": "Manage Beyond ID API credentials and integration settings.",
    "description": """
Retail IT - Beyond ID Manager
==============================
Configure and manage Beyond ID API credentials and RFID inventory integration
settings from Odoo's General Settings screen.

This module is proprietary to Retail IT. All rights reserved — see LICENSE
for full terms. It is licensed for use only by the client(s) for whom it was
deployed and may not be redistributed, sublicensed, or reused on other
installations without Retail IT's prior written consent.
""",
    "author": "Retail IT",
    "maintainer": "Retail IT",
    "license": "Other proprietary",
    "depends": ["base", "base_setup"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}

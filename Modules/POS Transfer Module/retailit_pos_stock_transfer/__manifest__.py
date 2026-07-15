# -*- coding: utf-8 -*-
{
    "name": "Retail IT POS Stock Transfer",
    "version": "19.0.1.0.0",
    "category": "Point of Sale",
    "summary": "Send and receive internal stock transfers directly from the POS screen",
    "description": """
Retail IT POS Stock Transfer
=============================

Lets cashiers create and receive internal stock transfers without leaving
the Point of Sale screen.

Configuration:

* Point of Sale > Configuration > Transfer Locations - a single global
  list of stock locations enabled for transfers, each with the operation
  type used when goods arrive there. Add a location once and it is
  available as a transfer destination on every POS terminal.
* Point of Sale > Assign Transfer Locations - a simple editable list to set
  which Transfer Location each POS terminal represents. Leave a terminal's
  Transfer Location empty to disable stock transfers on that terminal.

Point of Sale features:

* "Send Stock" - build a transfer from the current cart to any other
  configured location, capture who requested it, review/add-to/cancel
  transfers already sent but not yet received, and print a packing slip.
* "Receive Stock" - review internal transfers and purchase receipts
  addressed to this POS, validate with an employee PIN, and print a
  receiving slip.
""",
    "author": "Retail IT",
    "maintainer": "Retail IT",
    "license": "Other proprietary",
    "depends": ["base", "point_of_sale", "stock", "hr"],
    "data": [
        "security/ir.model.access.csv",
        "views/pos_transfer_location_views.xml",
        "views/pos_config_transfer_views.xml",
        "views/stock_picking_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "retailit_pos_stock_transfer/static/src/css/pos_stock_transfer.css",
            "retailit_pos_stock_transfer/static/src/js/print_slip.js",
            "retailit_pos_stock_transfer/static/src/js/control_buttons_patch.js",
            "retailit_pos_stock_transfer/static/src/js/popups/confirmation_popup.js",
            "retailit_pos_stock_transfer/static/src/js/popups/employee_pin_popup.js",
            "retailit_pos_stock_transfer/static/src/js/popups/send_stock_popup.js",
            "retailit_pos_stock_transfer/static/src/js/popups/receive_stock_popup.js",
            "retailit_pos_stock_transfer/static/src/xml/control_buttons.xml",
            "retailit_pos_stock_transfer/static/src/xml/confirmation_popup.xml",
            "retailit_pos_stock_transfer/static/src/xml/employee_pin_popup.xml",
            "retailit_pos_stock_transfer/static/src/xml/send_stock_popup.xml",
            "retailit_pos_stock_transfer/static/src/xml/receive_stock_popup.xml",
        ],
    },
    "images": ["static/description/icon.png"],
    "installable": True,
    "application": False,
    "auto_install": False,
}

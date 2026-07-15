# -*- coding: utf-8 -*-
from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    transfer_requested_by = fields.Char(
        string="Requested By (POS)",
        help="Name of the staff member who requested this transfer from "
             "the Point of Sale.",
    )
    transfer_validated_by = fields.Many2one(
        "hr.employee",
        string="Validated By (POS)",
        help="Employee who validated this transfer or receipt from the "
             "Point of Sale.",
    )

# -*- coding: utf-8 -*-
from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    transfer_location_id = fields.Many2one(
        "retailit.pos.transfer.location",
        string="Transfer Location",
        help="Which entry in the global Transfer Locations list this POS "
             "terminal represents. Leave empty to disable stock transfers "
             "on this POS. Manage the list of available locations under "
             "Point of Sale > Configuration > Transfer Locations.",
    )

# -*- coding: utf-8 -*-
from odoo import fields, models


class PosTransferLocation(models.Model):
    """A stock location enabled for POS internal stock transfers, and the
    operation type used when goods arrive at it.

    This is a single global list, not per-Point-of-Sale: any location on
    this list can send to, or receive from, any other location on the
    list. Add a location here once (Point of Sale > Configuration >
    Transfer Locations) and it becomes available for transfers on every
    POS terminal - each terminal only needs to say which of these
    locations it itself is (see pos.config.transfer_location_id).
    """

    _name = "retailit.pos.transfer.location"
    _description = "POS Stock Transfer Location"
    _order = "sequence, id"
    _rec_name = "location_id"

    sequence = fields.Integer(default=10)
    location_id = fields.Many2one(
        "stock.location",
        string="Location",
        required=True,
        domain=[("usage", "=", "internal")],
    )
    picking_type_id = fields.Many2one(
        "stock.picking.type",
        string="Receiving Operation Type",
        required=True,
        domain=[("code", "=", "internal")],
        help="Operation type used when goods arrive at this location - "
             "whether sent by another POS terminal or received from a "
             "purchase order.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "location_uniq",
            "unique(location_id)",
            "This location is already in the Transfer Locations list.",
        ),
    ]

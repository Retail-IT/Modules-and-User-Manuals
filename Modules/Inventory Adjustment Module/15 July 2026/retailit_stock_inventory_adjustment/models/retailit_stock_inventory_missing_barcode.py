from odoo import fields, models


class RetailitStockInventoryMissingBarcode(models.Model):
    _name = "retailit.stock.inventory.missing.barcode"
    _description = "Missing RFID Barcode"
    _order = "create_date desc, barcode"

    inventory_id = fields.Many2one(
        comodel_name="retailit.stock.inventory",
        string="Inventory",
        required=True,
        ondelete="cascade",
        index=True,
    )
    barcode = fields.Char(string="Barcode", required=True, index=True)
    product_name = fields.Char(string="Product Name")
    qty = fields.Float(string="Qty", digits="Product Unit of Measure")
    source = fields.Selection([
        ("rfid", "RFID Import"),
        ("scan", "Manual Scan"),
        ("mixed", "RFID / Scan"),
    ], string="Source", default="scan", required=True, index=True)
    occurrence_count = fields.Integer(string="Occurrences", default=1)
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Location",
        related="inventory_id.location_id",
        store=True,
    )
    count_mode = fields.Selection(
        related="inventory_id.count_mode",
        string="Count Mode",
        store=True,
    )
    inventory_state = fields.Selection(
        related="inventory_id.state",
        string="Inventory State",
        store=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        related="inventory_id.company_id",
        store=True,
    )

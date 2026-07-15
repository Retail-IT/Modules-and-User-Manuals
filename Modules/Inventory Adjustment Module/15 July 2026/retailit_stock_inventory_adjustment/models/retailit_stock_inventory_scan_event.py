from odoo import fields, models


class RetailitStockInventoryScanEvent(models.Model):
    _name = "retailit.stock.inventory.scan.event"
    _description = "Inventory Scan Event"
    _order = "scanned_at desc, id desc"

    inventory_id = fields.Many2one(
        comodel_name="retailit.stock.inventory",
        string="Inventory",
        required=True,
        ondelete="cascade",
        index=True,
    )
    line_id = fields.Many2one(
        comodel_name="retailit.stock.inventory.line",
        string="Inventory Line",
        ondelete="set null",
        index=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        ondelete="set null",
        index=True,
    )
    barcode = fields.Char(string="Barcode", index=True)
    event_type = fields.Selection([
        ("lookup", "Lookup"),
        ("save", "Save"),
        ("missing", "Missing"),
        ("error", "Error"),
    ], string="Event Type", required=True, default="lookup")
    match_source = fields.Selection([
        ("inventory_item", "Inventory Item"),
        ("product_product", "Product Master"),
        ("missing", "Missing"),
    ], string="Match Source")
    previous_qty = fields.Float(string="Previous Qty", digits="Product Unit of Measure")
    new_qty = fields.Float(string="New Qty", digits="Product Unit of Measure")
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="User",
        required=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
    )
    scanned_at = fields.Datetime(string="Scanned At", default=fields.Datetime.now, required=True)
    message = fields.Char(string="Message")

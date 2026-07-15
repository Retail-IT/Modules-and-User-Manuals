from odoo import fields, models


class RetailitStockInventoryMissedItem(models.Model):
    _name = "retailit.stock.inventory.missed.item"
    _description = "Inventory Missed Item"
    _order = "product_id"

    inventory_id = fields.Many2one(
        comodel_name="retailit.stock.inventory",
        string="Inventory",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
        ondelete="cascade",
        index=True,
    )
    barcode = fields.Char(string="Barcode", index=True)
    default_code = fields.Char(
        string="Internal Reference",
        related="product_id.default_code",
        store=True,
    )
    categ_id = fields.Many2one(
        comodel_name="product.category",
        string="Category",
        related="product_id.categ_id",
        store=True,
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Location",
        required=True,
        ondelete="cascade",
        index=True,
    )
    theoretical_qty = fields.Float(string="Theoretical Qty", digits="Product Unit of Measure")
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

    _inventory_product_location_uniq = models.Constraint(
        "UNIQUE(inventory_id, product_id, location_id)",
        "A product can only be listed once as missed per inventory and location.",
    )

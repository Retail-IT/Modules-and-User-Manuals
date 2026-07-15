from odoo import fields, models


class BeyondIdProductSyncIssue(models.Model):
    _name = "retailit.beyondid.product.sync.issue"
    _description = "Beyond ID Product Sync Issue"
    _order = "create_date desc, id desc"

    run_id = fields.Many2one(
        "retailit.beyondid.product.sync.run",
        required=True,
        ondelete="cascade",
        index=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product Variant",
        ondelete="set null",
        index=True,
    )
    level = fields.Selection(
        [
            ("warning", "Warning"),
            ("error", "Error"),
            ("skipped", "Skipped"),
            ("info", "Info"),
        ],
        required=True,
        index=True,
    )
    operation = fields.Selection(
        [
            ("import", "Import"),
            ("delete", "Delete"),
            ("local", "Local Validation"),
        ],
        required=True,
        index=True,
    )
    reason = fields.Char(index=True)
    code = fields.Char(index=True)
    message = fields.Text(required=True)
    product_key = fields.Char(index=True)
    raw_line = fields.Text()

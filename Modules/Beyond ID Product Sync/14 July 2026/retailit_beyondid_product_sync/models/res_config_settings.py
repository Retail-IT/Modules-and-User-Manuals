from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    beyondid_product_auto_sync = fields.Boolean(
        string="Enable Automatic Product Sync",
        config_parameter="retailit_beyondid_product_sync.auto_sync_enabled",
        help=(
            "When enabled, Odoo automatically sends products that need an update in Beyond ID. "
            "This includes new products, edited products, and failed retries. "
            "Products already synchronized with no changes are ignored."
        ),
    )
    beyondid_product_cron_limit = fields.Integer(
        string="Products per Automatic Sync",
        default=500,
        config_parameter="retailit_beyondid_product_sync.cron_limit",
        help=(
            "Maximum number of products Odoo will process each time the automatic sync runs. "
            "The selected products are sent to Beyond ID in internal API batches of 25 by default."
        ),
    )
    beyondid_product_batch_size = fields.Integer(
        string="Max Products per API Request",
        default=25,
        config_parameter="retailit_beyondid_product_sync.batch_size",
        help=(
            "Maximum number of product rows sent in one Beyond ID upload request. "
            "The recommended default is 25 to avoid long-running Beyond ID upload requests. "
            "If a manual or automatic run selects more products than this value, Odoo splits them into multiple API requests."
        ),
    )

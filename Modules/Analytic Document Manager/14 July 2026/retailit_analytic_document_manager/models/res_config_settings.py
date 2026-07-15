from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_analytic_distribution = fields.Json(
        related='pos_config_id.analytic_distribution',
        readonly=False,
    )
    analytic_precision = fields.Integer(
        related='pos_config_id.analytic_precision',
    )

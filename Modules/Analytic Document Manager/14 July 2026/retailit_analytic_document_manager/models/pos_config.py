from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    analytic_distribution = fields.Json(
        string='Analytic Distribution',
        help="Default analytic split applied to orders rung up under this "
             "point of sale. Carried through to the invoice line when an "
             "order is invoiced, and to the sales journal entry lines "
             "generated when a session is closed. Individual order lines "
             "can still be given their own distribution, which takes "
             "priority over this default.",
    )
    analytic_precision = fields.Integer(
        string='Analytic Precision',
        compute='_compute_analytic_precision',
    )

    def _compute_analytic_precision(self):
        precision = self.env['decimal.precision'].precision_get('Percentage Analytic')
        for config in self:
            config.analytic_precision = precision

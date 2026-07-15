from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = 'pos.order.line'

    analytic_distribution = fields.Json(
        string='Analytic Distribution',
        help="Analytic split for this line. Pre-filled from the point of "
             "sale's default distribution when the line is created, but "
             "can be overridden per line.",
    )
    analytic_precision = fields.Integer(
        string='Analytic Precision',
        compute='_compute_analytic_precision',
    )

    def _compute_analytic_precision(self):
        precision = self.env['decimal.precision'].precision_get('Percentage Analytic')
        for line in self:
            line.analytic_precision = precision

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._fill_default_analytic_distribution()
        return lines

    def _fill_default_analytic_distribution(self):
        """Pre-fill each line with its point of sale's default
        distribution, unless the line already carries a distribution of
        its own (e.g. set explicitly by the cashier)."""
        for line in self:
            if line.analytic_distribution:
                continue

            default = line.order_id.config_id.analytic_distribution
            if default:
                line.analytic_distribution = default


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _get_invoice_lines_values(self, line_values, pos_order_line, *args, **kwargs):
        """Carry the order line's analytic distribution - falling back to
        the point of sale's default - onto the invoice line generated for
        it, so analytic lines are created when the invoice posts."""
        vals = super()._get_invoice_lines_values(line_values, pos_order_line, *args, **kwargs)

        distribution = (
            pos_order_line.analytic_distribution
            or pos_order_line.order_id.config_id.analytic_distribution
        )
        if distribution:
            vals['analytic_distribution'] = distribution

        return vals

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = ['sale.order', 'analytic.mixin']


    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)

        for order in orders:
            if not order.website_id:
                continue

            analytic = self.env['retailit.analytic.website.mapping'].sudo()._get_analytic_account_for_website(
                order.website_id
            )

            if analytic:
                order.analytic_distribution = {str(analytic.id): 100.0}
                order.action_apply_analytic_account()

        return orders

    def action_apply_analytic_account(self):
        for order in self:
            if not order.analytic_distribution:
                raise UserError(_('Please set an Analytic Distribution first.'))

            lines = order.order_line.filtered(
                lambda l: not l.display_type
            )

            lines.write({
                'analytic_distribution': order.analytic_distribution
            })

        return True

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()

        if self.analytic_distribution:
            vals['analytic_distribution'] = self.analytic_distribution

        return vals

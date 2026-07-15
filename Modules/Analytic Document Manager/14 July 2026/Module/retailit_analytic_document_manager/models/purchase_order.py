from odoo import _, fields, models
from odoo.exceptions import UserError

class PurchaseOrder(models.Model):
    _inherit = ['purchase.order', 'analytic.mixin']


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

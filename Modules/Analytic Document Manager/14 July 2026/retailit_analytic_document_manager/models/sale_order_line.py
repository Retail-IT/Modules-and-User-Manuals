from collections import defaultdict

from odoo import api, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._apply_order_analytic_account()
        return lines

    def write(self, vals):
        res = super().write(vals)

        # Don't re-trigger ourselves when this write IS the analytic
        # distribution being set (avoids recursion), and don't stomp on
        # a distribution someone just explicitly chose.
        if 'analytic_distribution' not in vals:
            self._apply_order_analytic_account()

        return res

    def _apply_order_analytic_account(self):
        """Apply the order's analytic account to any product line that
        doesn't already have a distribution set. Grouped by order to
        keep this to one write per order."""
        groups = defaultdict(lambda: self.browse())

        for line in self:
            if line.display_type:
                continue

            order = line.order_id

            if not order.analytic_distribution or line.analytic_distribution:
                continue

            groups[order] |= line

        for order, lines in groups.items():
            lines.write({
                'analytic_distribution': order.analytic_distribution
            })

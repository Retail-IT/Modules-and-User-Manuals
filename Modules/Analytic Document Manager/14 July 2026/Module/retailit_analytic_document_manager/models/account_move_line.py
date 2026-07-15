from collections import defaultdict

from odoo import api, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._apply_move_analytic_account()
        return lines

    def write(self, vals):
        res = super().write(vals)

        if 'analytic_distribution' not in vals:
            self._apply_move_analytic_account()

        return res

    def _apply_move_analytic_account(self):
        """Apply the invoice's analytic account to any product line that
        doesn't already have a distribution set. Grouped by move to keep
        this to one write per invoice."""
        groups = defaultdict(lambda: self.browse())

        for line in self:
            if line.display_type != 'product':
                continue

            move = line.move_id

            if not move.analytic_distribution or line.analytic_distribution:
                continue

            groups[move] |= line

        for move, lines in groups.items():
            lines.write({
                'analytic_distribution': move.analytic_distribution
            })

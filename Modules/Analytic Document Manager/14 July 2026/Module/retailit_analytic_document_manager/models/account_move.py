from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = ['account.move', 'analytic.mixin']


    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._auto_apply_analytic_account()
        return moves

    def action_apply_analytic_account(self):
        for move in self:
            if not move.analytic_distribution:
                raise UserError(_('Please set an Analytic Distribution first.'))

            lines = move.line_ids.filtered(
                lambda l: l.display_type == 'product'
            )

            if not lines:
                raise UserError(_('No product lines found.'))

            lines.write({
                'analytic_distribution': move.analytic_distribution
            })

        return True

    def action_open_apply_analytic_wizard(self):
        """Button-facing entry point on the account.move form. Opens a
        wizard rather than writing to move.analytic_distribution inline -
        see retailit.analytic.distribution.move.wizard's docstring for why."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'retailit.analytic.distribution.move.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_move_id': self.id},
        }

    def _auto_apply_analytic_account(self):
        """Silent counterpart to action_apply_analytic_account(), used by
        automatic flows (create, line changes). Never raises: an invoice
        with no analytic distribution configured, or no product lines yet,
        should simply be left alone rather than blocking creation/save."""
        for move in self:
            if not move.analytic_distribution:
                continue

            lines = move.line_ids.filtered(
                lambda l: l.display_type == 'product' and not l.analytic_distribution
            )

            if lines:
                lines.write({
                    'analytic_distribution': move.analytic_distribution
                })

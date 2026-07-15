from odoo import api, fields, models


class AnalyticDistributionMoveWizard(models.TransientModel):
    _name = 'retailit.analytic.distribution.move.wizard'
    _inherit = 'analytic.mixin'
    _description = 'Apply Analytic Distribution to a Journal Entry'

    move_id = fields.Many2one(
        'account.move',
        string='Journal Entry',
        required=True,
        default=lambda self: self.env.context.get('active_id'),
    )

    def action_apply(self):
        """Write the chosen distribution onto the move's header field, then
        reuse the existing action_apply_analytic_account() to push it down
        to the product lines.

        This is deliberately a wizard rather than an inline field on the
        account.move form: account_invoice_extract patches the move form's
        renderer globally (InvoiceExtractFormRenderer) to support OCR box
        selection, and its getBoxType() helper crashes when it encounters
        the analytic_distribution widget's internal nested field markup
        directly on a top-level (non-x2many) field - see the "Cannot read
        properties of undefined (reading 'fields')" bug. A TransientModel
        wizard form doesn't use that renderer, so it isn't exposed to
        that bug, now or if Odoo's OCR internals change shape later.
        """
        self.ensure_one()

        if self.analytic_distribution and self.move_id:
            self.move_id.analytic_distribution = self.analytic_distribution
            self.move_id.action_apply_analytic_account()

        return {'type': 'ir.actions.act_window_close'}

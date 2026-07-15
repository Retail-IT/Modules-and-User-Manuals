from odoo import api, fields, models


class AnalyticDistributionWizard(models.TransientModel):
    _name = 'retailit.analytic.distribution.wizard'
    _inherit = 'analytic.mixin'
    _description = 'Apply Analytic Distribution to Selected Bank Transactions'

    statement_line_ids = fields.Many2many(
        'account.bank.statement.line',
        relation='retailit_stmt_line_analytic_wizard_rel',
        column1='wizard_id',
        column2='statement_line_id',
        string='Bank Transactions',
        default=lambda self: self.env.context.get('active_ids', []),
    )
    statement_line_count = fields.Integer(
        string='Number of Transactions',
        compute='_compute_statement_line_count',
    )

    @api.depends('statement_line_ids')
    def _compute_statement_line_count(self):
        for wizard in self:
            wizard.statement_line_count = len(wizard.statement_line_ids)

    def action_apply(self):
        """Apply the chosen distribution to every selected statement line.
        Write logic lives on account.bank.statement.line; this wizard only
        collects the distribution via the native analytic_distribution
        widget."""
        self.ensure_one()

        if self.analytic_distribution and self.statement_line_ids:
            self.statement_line_ids.set_analytic_distribution_bank_statement_line(
                self.analytic_distribution
            )

        return {'type': 'ir.actions.act_window_close'}

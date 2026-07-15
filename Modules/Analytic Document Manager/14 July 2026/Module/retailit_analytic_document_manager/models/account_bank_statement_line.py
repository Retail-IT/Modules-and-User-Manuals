import logging

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    def set_analytic_distribution_bank_statement_line(self, analytic_distribution):
        """Bulk-apply an analytic distribution to every counterpart line of
        each statement line in self (remaining balance + already-split
        lines, e.g. a fee line and its auto-generated tax/VAT line),
        whether or not the statement line is already reconciled. Only the
        liquidity (bank) line is excluded.

        Reconciled lines are deliberately in scope: applying a reconcile
        model typically creates the fee/VAT split AND reconciles the
        transaction in the same step, so restricting this to unreconciled
        lines would make the button unusable for its main use case.
        edit_reconcile_line() would otherwise block edits to reconciled
        lines for users outside account.group_account_user; that guard is
        bypassed here via skip_account_review_check since this action only
        ever writes analytic_distribution, never account_id/tax_ids/
        partner_id/balance - the fields that guard exists to protect.

        Reuses edit_reconcile_line() (account_accountant) for the actual
        write, once per line, re-seeking the line list fresh before each
        edit rather than snapshotting once - edit_reconcile_line() removes
        and recreates the line it edits, so a stale id from before an
        earlier edit in the same loop can point at nothing.

        An explicit flush after each edit is required: without it, the
        next re-seek in the same request can miss what was just written,
        silently skipping later lines on the same transaction.

        edit_reconcile_line() deletes and recreates the line it edits.
        Deleting a tax line runs the tax lock date check
        (account.move.line._check_tax_lock_date()), which raises
        UserError - not ValidationError - if the line's move falls on or
        before the company's tax lock date. That must be caught here (or
        it aborts this whole per-statement-line loop, silently skipping
        every remaining line on this and any later statement line in the
        same call) but it must NOT be silently swallowed, since it means
        a line was left unmodified for a real, actionable reason.

        :param analytic_distribution: dict, e.g. {"12": 60.0, "14": 40.0}
        :return: recordset of statement lines that had at least one line
            successfully updated
        """
        updated = self.env['account.bank.statement.line']

        for statement_line in self:
            touched_any = False

            _liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
            max_iterations = len(suspense_lines + other_lines) + 1

            try:
                for _i in range(max_iterations):
                    _liquidity_lines, suspense_lines, other_lines = statement_line._seek_for_lines()
                    remaining = (suspense_lines + other_lines).filtered(
                        lambda l: l.analytic_distribution != analytic_distribution
                    )
                    if not remaining:
                        break

                    statement_line.with_context(
                        skip_account_review_check=True
                    ).edit_reconcile_line(
                        remaining[0].id,
                        {'analytic_distribution': analytic_distribution},
                    )
                    self.env.flush_all()
                    touched_any = True
            except UserError as exc:
                _logger.warning(
                    "set_analytic_distribution_bank_statement_line: statement "
                    "line %s left partially/un-updated: %s",
                    statement_line.id, exc,
                )

            if touched_any:
                updated |= statement_line

        return updated

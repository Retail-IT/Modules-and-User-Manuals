from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AnalyticWebsiteMapping(models.Model):
    _name = 'retailit.analytic.website.mapping'
    _description = 'Website to Analytic Account Mapping'
    _rec_name = 'website_id'

    website_id = fields.Many2one(
        'website',
        string='Website',
        required=True,
        index=True,
    )
    analytic_account_id = fields.Many2one(
        'account.analytic.account',
        string='Analytic Account',
        required=True,
    )
    company_id = fields.Many2one(
        related='website_id.company_id',
        string='Company',
        store=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'website_uniq',
            'unique(website_id)',
            'A mapping already exists for this website. '
            'Please edit the existing mapping instead of creating a new one.',
        ),
    ]

    @api.model
    def _get_analytic_account_for_website(self, website):
        """Return the account.analytic.account configured for the given
        website recordset, or an empty recordset if none is configured."""
        if not website:
            return self.env['account.analytic.account']

        mapping = self.search([
            ('website_id', '=', website.id),
        ], limit=1)

        return mapping.analytic_account_id

    @api.model
    def action_sweep_missing_analytic_accounts(self):
        """One-off bulk fix: scan every eCommerce Sales Order (and its
        invoices) for a configured website and backfill any missing
        analytic account / distribution. Records that already have one
        set are left untouched - this only ever fills in gaps."""
        mappings = self.sudo().search([])

        if not mappings:
            raise UserError(_('No website mappings are configured yet. '
                               'Set one up first, then run the sweep.'))

        website_analytic = {
            m.website_id.id: m.analytic_account_id for m in mappings
        }

        SaleOrder = self.env['sale.order'].sudo()
        orders = SaleOrder.search([
            ('website_id', 'in', list(website_analytic.keys())),
        ])

        orders_fixed = 0
        order_lines_fixed = 0

        for order in orders:
            analytic = website_analytic.get(order.website_id.id)
            if not analytic:
                continue

            distribution = {str(analytic.id): 100.0}
            changed = False

            if not order.analytic_distribution:
                order.analytic_distribution = distribution
                changed = True

            lines = order.order_line.filtered(
                lambda l: not l.display_type and not l.analytic_distribution
            )
            if lines:
                lines.write({
                    'analytic_distribution': distribution
                })
                order_lines_fixed += len(lines)
                changed = True

            if changed:
                orders_fixed += 1

        invoices = orders.invoice_ids.filtered(
            lambda m: m.move_type in ('out_invoice', 'out_refund')
        )

        invoices_fixed = 0
        invoice_lines_fixed = 0

        for move in invoices:
            distribution = move.analytic_distribution

            if not distribution:
                # Invoice predates the fix and never got the header field
                # copied across - fall back to the originating order.
                order = move.line_ids.sale_line_ids.order_id[:1]
                analytic = website_analytic.get(order.website_id.id) if order else False

                if analytic:
                    distribution = {str(analytic.id): 100.0}
                    move.analytic_distribution = distribution

            if not distribution:
                continue

            changed = False

            lines = move.line_ids.filtered(
                lambda l: l.display_type == 'product' and not l.analytic_distribution
            )
            if lines:
                lines.write({
                    'analytic_distribution': distribution
                })
                invoice_lines_fixed += len(lines)
                changed = True

            if changed:
                invoices_fixed += 1

        message = _(
            'Sales Orders updated: %(orders)s (lines fixed: %(order_lines)s)\n'
            'Invoices updated: %(invoices)s (lines fixed: %(invoice_lines)s)'
        ) % {
            'orders': orders_fixed,
            'order_lines': order_lines_fixed,
            'invoices': invoices_fixed,
            'invoice_lines': invoice_lines_fixed,
        }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Analytic Account Sweep Complete'),
                'message': message,
                'sticky': True,
                'type': 'success',
            },
        }

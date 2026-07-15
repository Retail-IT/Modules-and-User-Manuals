from odoo import models


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_sale_vals(self, key, sale_vals):
        """Carry the point of sale's default analytic distribution onto
        the sales journal entry lines generated when the session is
        closed, so revenue can be reported by analytic account."""
        vals = super()._get_sale_vals(key, sale_vals)

        distribution = self.config_id.analytic_distribution
        if distribution:
            vals['analytic_distribution'] = distribution

        return vals

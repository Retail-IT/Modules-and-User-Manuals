# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
"""Extend product.category with a category code used to generate SKU sequences."""
from odoo import api, fields, models


class ProductCategory(models.Model):
    """Add a category code and its backing ir.sequence for SKU generation."""
    _inherit = 'product.category'

    category_code = fields.Char(string="Category Code")

    _sql_constraints = [
        ('category_code_unique', 'unique(category_code)', 'Category Code must be unique!')
    ]

    @api.model
    def create(self, vals):
        record = super().create(vals)
        record._create_sequence_if_needed()
        return record

    def write(self, vals):
        result = super().write(vals)
        self._create_sequence_if_needed()
        return result

    def _create_sequence_if_needed(self):
        # NOTE: default of 0 added here (the original module read this
        # parameter without a default and would raise on int(None) the
        # first time a category code was set before the setting was saved).
        padding_seq = int(self.env["ir.config_parameter"].sudo().get_param(
            "retailit_sku_barcode_generator.sku_sequence_padding", 0
        ))

        for category in self:
            if category.category_code:
                seq_code = category.category_code
                sequence = self.env['ir.sequence'].search([('code', '=', seq_code)], limit=1)
                if not sequence:
                    self.env['ir.sequence'].create({
                        'name': f'{seq_code} Sequence',
                        'code': seq_code,
                        'prefix': f"{seq_code}-",
                        'padding': padding_seq,
                    })

    @api.onchange('category_code')
    def _onchange_category_code(self):
        """Warn if user tries to change category_code compared to saved value."""
        # Skip warning for new records (no ID yet)
        if not self._origin or not self._origin.id:
            return

        # Get the current saved category_code from DB
        current_code = self._origin.category_code or ""

        # Only show warning if there's an actual change from saved value
        if (self.category_code and
                self.category_code != current_code and
                current_code != ""):  # Only warn if there was a previously saved value
            return {
                'warning': {
                    'title': "Category Code Change",
                    'message': (
                        "Changing the category code only affects new products created under "
                        "this category. To update existing ones, regenerate their internal "
                        "references."
                    )
                }
            }

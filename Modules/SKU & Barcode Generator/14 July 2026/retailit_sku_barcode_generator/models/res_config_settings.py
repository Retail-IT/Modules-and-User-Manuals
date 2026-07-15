# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
"""Configuration settings for barcode generation and SKU (internal reference) generation."""
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    """Settings for barcode and SKU generation."""
    _inherit = "res.config.settings"

    # --- Barcode generation settings ---
    generate_barcode_on_product = fields.Boolean(
        string="Generate Product Barcode On Product Create?",
        related="company_id.generate_barcode_on_product",
        readonly=False,
    )

    sh_barcode_type = fields.Selection(
        related="company_id.sh_barcode_type",
        string="Barcode Type (Product)",
        readonly=False,
    )

    # --- SKU (internal reference) generation settings ---
    sku_sequence_padding = fields.Integer(
        string="SKU Sequence Padding",
        config_parameter="retailit_sku_barcode_generator.sku_sequence_padding",
        help="Number of digits for SKU sequence padding.",
    )

    @api.onchange('sku_sequence_padding')
    def _onchange_sku_sequence_padding(self):
        """Warn that padding changes only apply to sequences created after the change."""
        current_padding = int(self.env["ir.config_parameter"].sudo().get_param(
            "retailit_sku_barcode_generator.sku_sequence_padding", 0
        ))

        if self.sku_sequence_padding and self.sku_sequence_padding != current_padding:
            return {
                'warning': {
                    'title': "SKU Padding Change",
                    'message': (
                        "Changing padding won't affect existing product SKUs. It applies only "
                        "when a new category code is added after the change. Regenerate "
                        "references to apply the new padding to existing SKUs."
                    )
                }
            }

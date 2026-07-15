# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
"""Extend product.template with barcode generation and SKU (internal reference) generation."""
import base64
import werkzeug.exceptions
from odoo import fields, models
from . import barcode_helper


class ProductTemplate(models.Model):
    """Extends product.template to add barcode and SKU generation."""
    _inherit = 'product.template'

    sh_product_barcode_img = fields.Binary(
        string="Barcode Image", readonly=True)

    # ----------------------------------------------------------
    # BARCODE - ACTIONS
    # ----------------------------------------------------------

    def action_generate_barcode_image(self):
        """Generates and sets the barcode image for the product template."""
        self.ensure_one()
        if self.barcode:
            img_barcode = self.env['ir.actions.report'].barcode(
                'Code128', self.barcode, width=500, height=90, humanreadable=0)
            self.sh_product_barcode_img = base64.b64encode(img_barcode)

    def generate_barcode_image(self, ean):
        """Generates a barcode image from the given EAN and attaches it to the product."""
        try:
            ean_barcode = self.env['ir.actions.report'].barcode(
                'EAN13', ean, width=500, height=90, humanreadable=0)
            if ean_barcode:
                self.sh_product_barcode_img = base64.b64encode(ean_barcode)

        except (ValueError, AttributeError) as exc:
            raise werkzeug.exceptions.HTTPException(
                description='Cannot convert into barcode.'
            ) from exc

    def action_generate_barcode_image_multi(self):
        """Mass action: Generate barcode image for selected templates"""
        for template in self:
            if not template.barcode:
                continue

            try:
                barcode_str = str(template.barcode).strip()

                if len(barcode_str) == 13 and barcode_str.isdigit():
                    barcode_type = 'EAN13'
                elif len(barcode_str) == 8 and barcode_str.isdigit():
                    barcode_type = 'EAN8'
                else:
                    barcode_type = 'Code128'

                barcode_img = self.env['ir.actions.report'].barcode(
                    barcode_type, barcode_str, width=500, height=90, humanreadable=0
                )
                if barcode_img:
                    template.sh_product_barcode_img = base64.b64encode(barcode_img)

            except Exception:
                pass

    def action_generate_barcode(self):
        """Generates a new barcode for the product template and updates the barcode image."""
        if self:
            for rec in self:
                ean = barcode_helper.generate_unique_ean(self.env, self.env.company.sh_barcode_type)
                rec.barcode = ean
                rec.generate_barcode_image(ean)

    # ----------------------------------------------------------
    # SKU (INTERNAL REFERENCE) - ACTIONS
    # ----------------------------------------------------------

    def action_generate_skus(self):
        """Generates internal references (SKUs) for this template's variant(s)
        from the category's sequence."""
        for template in self:
            category = template.categ_id
            if not category:
                continue

            seq_code = category.category_code

            # Skip if category_code is not set
            if not seq_code:
                continue

            sequence = self.env['ir.sequence'].search([('code', '=', seq_code)], limit=1)

            if not sequence:
                continue

            # Handle single variant case
            if len(template.product_variant_ids) == 1:
                variant = template.product_variant_ids[0]
                variant.default_code = sequence.next_by_code(seq_code)
                continue

            # Handle multiple variants
            for variant in template.product_variant_ids:
                # Skip if variant already has a code starting with the sequence code
                if variant.default_code and variant.default_code.startswith(seq_code):
                    continue

                # Generate new code
                variant.default_code = sequence.next_by_code(seq_code)

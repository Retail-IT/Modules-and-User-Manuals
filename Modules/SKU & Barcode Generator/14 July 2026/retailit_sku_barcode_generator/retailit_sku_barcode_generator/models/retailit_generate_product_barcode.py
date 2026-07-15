# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
"""Wizard to generate product barcodes."""
from odoo import fields, models, _
from odoo.exceptions import UserError
from . import barcode_helper


class RetailitGenerateProductBarcode(models.Model):
    """Wizard to generate product barcodes."""
    _name = 'retailit.generate.product.barcode'
    _description = 'Generate Product Barcode'

    # Generate Barcode for Existing Product
    overwrite_existing = fields.Boolean("Overwrite Barcode If Exists")

    def generate_barcode(self):
        """Generates barcodes for selected products."""
        if self.env.user.has_groups(
                'retailit_sku_barcode_generator.group_barcode_generator'
        ):

            context = dict(self._context or {})
            active_ids = context.get('active_ids', []) or []
            active_model = context.get('active_model', []) or []

            if active_model == 'product.product':
                for record in self.env['product.product'].browse(active_ids):

                    new_barcode = ''
                    if record.id:
                        new_barcode = barcode_helper.generate_unique_ean(
                            self.env, self.env.company.sh_barcode_type)
                        if self.overwrite_existing:  # Overwrite existing
                            record.barcode = new_barcode
                            record.generate_barcode_image(new_barcode)
                        else:
                            if not record.barcode:  # Don't overwrite existing barcodes Else generate New
                                record.barcode = new_barcode
                                record.generate_barcode_image(new_barcode)

            elif active_model == 'product.template':
                for record in self.env['product.template'].browse(active_ids):
                    new_barcode = ''
                    if record.id:
                        new_barcode = barcode_helper.generate_unique_ean(
                            self.env, self.env.company.sh_barcode_type)
                        if self.overwrite_existing:  # Overwrite existing
                            record.barcode = new_barcode
                            record.generate_barcode_image(new_barcode)
                        else:
                            if not record.barcode:  # Don't overwrite existing barcodes Else generate New
                                record.barcode = new_barcode
                                record.generate_barcode_image(new_barcode)

            return {'type': 'ir.actions.act_window_close'}

        else:
            raise UserError(_(
                "You don't have rights to generate product barcode"))

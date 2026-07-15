# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
"""Extend product.product with barcode generation and SKU (internal reference) generation."""
import base64
import werkzeug.exceptions
from odoo import api, fields, models
from . import barcode_helper


class ProductProduct(models.Model):
    _inherit = 'product.product'

    sh_product_barcode_img = fields.Binary(
        string="Barcode Image",
        readonly=True,
        attachment=True
    )

    # Hard safety net: even though generation retries on a collision (see
    # barcode_helper.generate_unique_ean), this DB-level constraint is what
    # actually guarantees uniqueness under concurrent requests, and also
    # catches duplicates a user types in manually. Odoo stores an unset
    # Char field as NULL, and Postgres treats multiple NULLs as distinct,
    # so products without a barcode are not affected.
    _sql_constraints = [
        (
            'retailit_barcode_uniq',
            'unique(barcode)',
            'This barcode is already assigned to another product. Barcodes must be unique.',
        ),
    ]

    # ----------------------------------------------------------
    # BARCODE - HELPER METHODS
    # ----------------------------------------------------------

    def _get_barcode_type(self, barcode):
        barcode_str = str(barcode).strip()
        if barcode_str.isdigit():
            if len(barcode_str) == 13:
                return 'EAN13', barcode_str
            if len(barcode_str) == 8:
                return 'EAN8', barcode_str
        return 'Code128', barcode_str

    def _generate_barcode_image(self, barcode):
        barcode_type, barcode_str = self._get_barcode_type(barcode)
        return self.env['ir.actions.report'].barcode(
            barcode_type,
            barcode_str,
            width=500,
            height=90,
            humanreadable=0
        )

    # ----------------------------------------------------------
    # BARCODE - ACTIONS
    # ----------------------------------------------------------

    def action_generate_barcode_image(self):
        self.ensure_one()
        if not self.barcode:
            return

        try:
            barcode_img = self._generate_barcode_image(self.barcode)
            if barcode_img:
                self.write({
                    'sh_product_barcode_img': base64.b64encode(barcode_img)
                })
        except Exception:
            raise werkzeug.exceptions.HTTPException(
                description='Cannot convert into barcode.'
            )

    def action_generate_barcode(self):
        for product in self:
            ean = barcode_helper.generate_unique_ean(self.env, self.env.company.sh_barcode_type)
            product.barcode = ean
            product.generate_barcode_image(ean)

    def generate_barcode_image(self, ean):
        try:
            barcode_img = self._generate_barcode_image(ean)
            if barcode_img:
                self.write({
                    'sh_product_barcode_img': base64.b64encode(barcode_img)
                })
        except Exception:
            raise werkzeug.exceptions.HTTPException(
                description='Cannot convert into barcode.'
            )

    def action_generate_barcode_image_multi(self):
        """Mass action: Generate barcode image for selected products"""
        for product in self.filtered('barcode'):
            try:
                barcode_img = product._generate_barcode_image(product.barcode)
                if barcode_img:
                    product.write({
                        'sh_product_barcode_img': base64.b64encode(barcode_img)
                    })
            except Exception:
                continue

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)

        if (
            self.env.user.has_groups('retailit_sku_barcode_generator.group_barcode_generator')
            and self.env.company.generate_barcode_on_product
        ):
            for product in products.filtered(lambda p: not p.barcode):
                ean = barcode_helper.generate_unique_ean(self.env, self.env.company.sh_barcode_type)
                product.barcode = ean
                product.generate_barcode_image(ean)

        return products

    # ----------------------------------------------------------
    # SKU (INTERNAL REFERENCE) - ACTIONS
    # ----------------------------------------------------------

    def action_generate_template_internal_reference(self):
        """Button / server action to generate SKU from category sequence"""
        for product in self:
            category = product.categ_id
            if not category or not category.category_code:
                continue

            seq_code = category.category_code
            sequence = self.env['ir.sequence'].search([('code', '=', seq_code)], limit=1)

            if sequence:
                product.default_code = sequence.next_by_code(seq_code)

# -*- coding: utf-8 -*-
# Copyright (C) Retail IT.
"""Barcode generation helper functions (not an Odoo model)."""
import random
import barcode

from odoo import _
from odoo.exceptions import UserError

MAX_GENERATION_ATTEMPTS = 20


def get_random_number():
    """Return a random 12-digit string used as the base for an EAN barcode."""
    random_num = str(random.randint(10000000000, 99999999999))
    random_first_digit = random.randint(1, 9)
    random_str = str(random_first_digit) + ''.join(map(str, random_num[:11]))
    return random_str


def generate_ean(barcode_type):
    """Generate a barcode of the specified type with a random EAN.

    NOTE: this does not check uniqueness against existing products.
    Use generate_unique_ean() from model code instead, unless you have
    already checked uniqueness yourself.
    """
    ean_class = barcode.get_barcode_class(barcode_type)
    ean = ean_class(get_random_number())
    return ean.get_fullcode()


def generate_unique_ean(env, barcode_type, max_attempts=MAX_GENERATION_ATTEMPTS):
    """Generate a barcode and confirm it isn't already used by any product,
    retrying on collision. Checks both product.product and product.template
    (including archived records, and across companies, since barcodes must
    be globally unique regardless of company or active state), and uses
    sudo() so the check itself is never blocked by record rules.
    """
    Product = env['product.product'].sudo().with_context(active_test=False)
    Template = env['product.template'].sudo().with_context(active_test=False)

    for _attempt in range(max_attempts):
        candidate = generate_ean(barcode_type)
        if Product.search_count([('barcode', '=', candidate)]):
            continue
        if Template.search_count([('barcode', '=', candidate)]):
            continue
        return candidate

    raise UserError(_(
        "Could not generate a unique barcode after %s attempts. "
        "Please try again."
    ) % max_attempts)

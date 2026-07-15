# -*- coding: utf-8 -*-
"""
Pre-init hook for retailit_stock_inventory_adjustment.

Runs before Odoo loads the module's models on both fresh install and upgrade.
Safely adds any columns that the models expect to exist, so that the ORM
never encounters missing columns regardless of how the deployment is triggered.
"""
import logging

_logger = logging.getLogger(__name__)


def pre_init_hook(env):
    """Add Google Sheets token columns to res_users if they do not already exist.

    This runs before the ORM registers the model fields, so the columns are
    guaranteed to be in place before any query touches res_users.
    """
    cr = env.cr
    columns = [
        'google_sheets_access_token',
        'google_sheets_refresh_token',
        'google_sheets_token_expiry',
    ]
    for col in columns:
        cr.execute(
            """
            ALTER TABLE res_users
            ADD COLUMN IF NOT EXISTS %s VARCHAR
            """ % col  # column name cannot be parameterised in DDL
        )
        _logger.info(
            'retailit_stock_inventory_adjustment: ensured column res_users.%s exists', col
        )

# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    google_oauth_client_id = fields.Char(
        string="Google OAuth Client ID",
        config_parameter='retailit_stock_inventory_adjustment.google_oauth_client_id',
        help="The Client ID from your Google Cloud OAuth 2.0 credentials. "
             "Found in Google Cloud Console → APIs & Services → Credentials.",
    )
    google_oauth_client_secret = fields.Char(
        string="Google OAuth Client Secret",
        config_parameter='retailit_stock_inventory_adjustment.google_oauth_client_secret',
        help="The Client Secret from your Google Cloud OAuth 2.0 credentials.",
    )
    google_sheets_folder_id = fields.Char(
        string="Google Drive Folder ID (optional)",
        config_parameter='retailit_stock_inventory_adjustment.google_sheets_folder_id',
        help="If set, exported sheets will be created inside this Google Drive folder. "
             "Find the ID at the end of the folder URL: "
             "drive.google.com/drive/folders/<FOLDER_ID>",
    )

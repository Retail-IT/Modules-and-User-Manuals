# -*- coding: utf-8 -*-
import json
import urllib.parse
import urllib.request
import urllib.error
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class GoogleOAuthCallback(http.Controller):

    @http.route(
        '/retailit_stock_inventory_adjustment/google_oauth_callback',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
    )
    def google_oauth_callback(self, code=None, state=None, error=None, **kwargs):
        """Handle the OAuth 2.0 callback from Google."""

        if error:
            _logger.warning("Google OAuth error: %s", error)
            return request.redirect('/odoo/inventory?oauth_error=%s' % urllib.parse.quote(error))

        if not code:
            return request.redirect('/odoo/inventory?oauth_error=no_code')

        ICP = request.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('retailit_stock_inventory_adjustment.google_oauth_client_id')
        client_secret = ICP.get_param('retailit_stock_inventory_adjustment.google_oauth_client_secret')
        base_url = ICP.get_param('web.base.url').rstrip('/')
        redirect_uri = base_url + '/retailit_stock_inventory_adjustment/google_oauth_callback'

        # Exchange authorisation code for tokens
        post_data = urllib.parse.urlencode({
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://oauth2.googleapis.com/token',
            data=post_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                token_data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8')
            _logger.error("Token exchange failed: %s", err_body)
            return request.redirect('/odoo/inventory?oauth_error=token_exchange_failed')

        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)

        if not access_token:
            return request.redirect('/odoo/inventory?oauth_error=no_access_token')

        # Save tokens via ir.config_parameter (no schema migration needed)
        from odoo.addons.retailit_stock_inventory_adjustment.models.retailit_stock_inventory_google_sheets import save_tokens
        save_tokens(request.env, request.env.uid, access_token, refresh_token, expires_in)

        # Redirect back to the inventory record that triggered the flow
        inventory_id = state
        if inventory_id:
            try:
                inv_id = int(inventory_id)
                return request.redirect('/odoo/inventory/%d' % inv_id)
            except (ValueError, TypeError):
                pass

        return request.redirect('/odoo/inventory')

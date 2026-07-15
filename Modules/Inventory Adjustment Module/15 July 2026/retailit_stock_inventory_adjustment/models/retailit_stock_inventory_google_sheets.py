# -*- coding: utf-8 -*-
"""
Google Sheets export for inventory adjustments.

Authentication: OAuth 2.0 per-user flow.
- Admin configures OAuth client ID + secret once in Settings
- Each user clicks "Google Sheets" → redirected to Google consent screen once
- Tokens stored in ir.config_parameter keyed by user ID (no schema migration needed)
- Token refreshed automatically
- Zero external pip dependencies — uses Python stdlib only (urllib, json)
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import logging

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

SHEETS_SCOPE = (
    'https://www.googleapis.com/auth/spreadsheets '
    'https://www.googleapis.com/auth/drive.file'
)

_TOKEN_KEY = 'retailit_stock_inventory_adjustment.gsheets_token.%s.%s'

# ── Colours matching the Excel report ────────────────────────────────────────
COL_DARK    = {'red': 0.204, 'green': 0.227, 'blue': 0.251}   # #343a40
COL_GREY    = {'red': 0.914, 'green': 0.925, 'blue': 0.937}   # #e9ecef
COL_LIGHT   = {'red': 0.973, 'green': 0.976, 'blue': 0.980}   # #f8f9fa
COL_RED     = {'red': 0.863, 'green': 0.208, 'blue': 0.271}   # #dc3545
COL_GREEN   = {'red': 0.157, 'green': 0.655, 'blue': 0.271}   # #28a745
COL_WHITE   = {'red': 1.0,   'green': 1.0,   'blue': 1.0}


# ── Stdlib-only Google API HTTP client ────────────────────────────────────────

def _google_api(method, url, token, body=None, params=None):
    if params:
        url = url + '?' + urllib.parse.urlencode(params)
    data = None
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8')
        raise UserError("Google API error (%s): %s" % (url.split('?')[0], body_text))


def _refresh_access_token(client_id, client_secret, refresh_token):
    data = urllib.parse.urlencode({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://oauth2.googleapis.com/token',
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        _logger.error("Token refresh error: %s", e.read().decode('utf-8'))
        return {'error': 'refresh_failed'}


# ── Token storage helpers (ir.config_parameter) ───────────────────────────────

def _token_get(env, user_id, key):
    return env['ir.config_parameter'].sudo().get_param(_TOKEN_KEY % (user_id, key)) or None

def _token_set(env, user_id, values):
    ICP = env['ir.config_parameter'].sudo()
    for key, value in values.items():
        ICP.set_param(_TOKEN_KEY % (user_id, key), value or '')

def _token_clear(env, user_id):
    _token_set(env, user_id, {'access_token': '', 'refresh_token': '', 'expiry': ''})

def get_valid_token(env, user_id):
    ICP = env['ir.config_parameter'].sudo()
    client_id     = ICP.get_param('retailit_stock_inventory_adjustment.google_oauth_client_id')
    client_secret = ICP.get_param('retailit_stock_inventory_adjustment.google_oauth_client_secret')
    access_token  = _token_get(env, user_id, 'access_token')
    expiry        = _token_get(env, user_id, 'expiry')
    refresh_token = _token_get(env, user_id, 'refresh_token')

    if access_token and expiry:
        try:
            if int(expiry) > int(time.time()):
                return access_token
        except (ValueError, TypeError):
            pass

    if refresh_token:
        result = _refresh_access_token(client_id, client_secret, refresh_token)
        if 'access_token' in result:
            _token_set(env, user_id, {
                'access_token': result['access_token'],
                'expiry': str(int(time.time()) + int(result.get('expires_in', 3600)) - 60),
            })
            return result['access_token']
        _logger.warning("Google Sheets token refresh failed for user %s", user_id)
        _token_clear(env, user_id)

    return None

def save_tokens(env, user_id, access_token, refresh_token, expires_in):
    _token_set(env, user_id, {
        'access_token':  access_token,
        'refresh_token': refresh_token,
        'expiry':        str(int(time.time()) + int(expires_in) - 60),
    })


# ── Sheets formatting helpers ─────────────────────────────────────────────────

def _cell_fmt(bold=False, font_size=10, font_color=None, bg_color=None,
              h_align=None, number_fmt=None, borders=False):
    """Build a Google Sheets CellFormat dict."""
    fmt = {}
    tf = {}
    if bold:
        tf['bold'] = True
    if font_size != 10:
        tf['fontSize'] = font_size
    if font_color:
        tf['foregroundColorStyle'] = {'rgbColor': font_color}
    if tf:
        fmt['textFormat'] = tf
    if bg_color:
        fmt['backgroundColorStyle'] = {'rgbColor': bg_color}
    if h_align:
        fmt['horizontalAlignment'] = h_align
    if number_fmt:
        fmt['numberFormat'] = {'type': 'NUMBER', 'pattern': number_fmt}
    if borders:
        b = {'style': 'SOLID', 'colorStyle': {'rgbColor': {'red': 0.6, 'green': 0.6, 'blue': 0.6}}}
        fmt['borders'] = {'top': b, 'bottom': b, 'left': b, 'right': b}
    return fmt

def _repeat_cell(sheet_id, r1, r2, c1, c2, fmt):
    return {
        'repeatCell': {
            'range': {'sheetId': sheet_id, 'startRowIndex': r1, 'endRowIndex': r2,
                      'startColumnIndex': c1, 'endColumnIndex': c2},
            'cell': {'userEnteredFormat': fmt},
            'fields': 'userEnteredFormat(%s)' % ','.join(fmt.keys()),
        }
    }

def _col_width(sheet_id, col, pixels):
    return {
        'updateDimensionProperties': {
            'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                      'startIndex': col, 'endIndex': col + 1},
            'properties': {'pixelSize': pixels},
            'fields': 'pixelSize',
        }
    }

def _freeze(sheet_id, rows=1):
    return {
        'updateSheetProperties': {
            'properties': {'sheetId': sheet_id, 'gridProperties': {'frozenRowCount': rows}},
            'fields': 'gridProperties.frozenRowCount',
        }
    }

def _merge(sheet_id, r, c1, c2):
    return {
        'mergeCells': {
            'range': {'sheetId': sheet_id, 'startRowIndex': r, 'endRowIndex': r + 1,
                      'startColumnIndex': c1, 'endColumnIndex': c2},
            'mergeType': 'MERGE_ALL',
        }
    }


# ── Inventory model extension ─────────────────────────────────────────────────

class RetailitStockInventoryGoogleSheets(models.Model):
    _inherit = 'retailit.stock.inventory'

    def _get_google_oauth_redirect_url(self, inventory_id):
        ICP = self.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('retailit_stock_inventory_adjustment.google_oauth_client_id')
        if not client_id:
            raise UserError(
                "Google Sheets is not configured. "
                "Please ask your administrator to add the OAuth Client ID and Secret "
                "under Settings → Inventory → Google Sheets Integration."
            )
        base_url = ICP.get_param('web.base.url').rstrip('/')
        redirect_uri = base_url + '/retailit_stock_inventory_adjustment/google_oauth_callback'
        params = urllib.parse.urlencode({
            'client_id':     client_id,
            'redirect_uri':  redirect_uri,
            'response_type': 'code',
            'scope':         SHEETS_SCOPE,
            'access_type':   'offline',
            'prompt':        'consent',
            'state':         str(inventory_id),
        })
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + params

    def _build_sheet1_data(self, inventory):
        """Return (rows_of_values, format_requests, merge_requests) for the Inventory sheet."""
        _ = self.env._
        rows = []
        fmt_requests = []
        merge_requests = []
        SID = 0  # sheetId for Inventory sheet

        def r():
            return len(rows)

        # ── Title ────────────────────────────────────────────────────────────
        rows.append([_('Inventory Adjustment: %s') % inventory.name, '', '', '', '', ''])
        merge_requests.append(_merge(SID, r() - 1, 0, 6))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
            bold=True, font_size=16, font_color=COL_WHITE, bg_color=COL_DARK, h_align='CENTER')))

        rows.append([''] * 6)

        # ── Info rows ────────────────────────────────────────────────────────
        rows.append([_('Location:'), inventory.location_id.display_name or '',
                     _('Date:'), str(inventory.date.date()) if inventory.date else '', '', ''])
        merge_requests.append(_merge(SID, r() - 1, 3, 6))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 1, _cell_fmt(bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 1, 2, _cell_fmt(borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 2, 3, _cell_fmt(bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 3, 6, _cell_fmt(borders=True)))

        rows.append([_('Type:'),
                     dict(inventory._fields['inventory_type'].selection).get(inventory.inventory_type, ''),
                     _('State:'),
                     dict(inventory._fields['state'].selection).get(inventory.state, ''), '', ''])
        merge_requests.append(_merge(SID, r() - 1, 3, 6))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 1, _cell_fmt(bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 1, 2, _cell_fmt(borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 2, 3, _cell_fmt(bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 3, 6, _cell_fmt(borders=True)))

        rows.append([''] * 6)

        # ── All Lines section ─────────────────────────────────────────────────
        rows.append([_('All Lines'), '', '', '', '', ''])
        merge_requests.append(_merge(SID, r() - 1, 0, 6))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
            bold=True, font_size=14, font_color=COL_WHITE, bg_color=COL_DARK)))

        rows.append([_('Product'), _('Location'), _('Lot/Serial'),
                     _('Theoretical'), _('Counted'), _('Difference')])
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
            bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))

        lines_by_location = {}
        for line in inventory.line_ids:
            loc = line.location_id.display_name
            lines_by_location.setdefault(loc, []).append(line)

        for loc_name, lines in lines_by_location.items():
            total_theo  = sum(l.theoretical_qty for l in lines)
            total_count = sum(l.product_qty for l in lines)
            total_diff  = sum(l.difference_qty for l in lines)
            rows.append([
                "%s | %s: %.2f | %s: %.2f | %s: %.2f" % (
                    loc_name, _('Theoretical'), total_theo,
                    _('Counted'), total_count, _('Diff'), total_diff),
                '', '', '', '', ''])
            merge_requests.append(_merge(SID, r() - 1, 0, 6))
            fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
                bold=True, font_color=COL_WHITE, bg_color=COL_DARK, borders=True)))

            for line in lines:
                diff = line.difference_qty
                rows.append([
                    line.product_id.display_name,
                    line.location_id.display_name,
                    line.lot_id.display_name if line.lot_id else '',
                    line.theoretical_qty,
                    line.product_qty,
                    diff,
                ])
                row_i = r() - 1
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 0, 1, _cell_fmt(bold=True, bg_color=COL_LIGHT, borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 1, 3, _cell_fmt(borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 3, 5, _cell_fmt(h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
                if diff < 0:
                    fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 5, 6, _cell_fmt(bold=True, font_color=COL_RED, h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
                elif diff > 0:
                    fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 5, 6, _cell_fmt(bold=True, font_color=COL_GREEN, h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
                else:
                    fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 5, 6, _cell_fmt(h_align='RIGHT', number_fmt='#,##0.00', borders=True)))

            rows.append([''] * 6)

        # ── Shortages section ─────────────────────────────────────────────────
        shortage_lines = inventory.line_ids.filtered(lambda l: l.difference_qty < 0)
        if shortage_lines:
            rows.append([''] * 6)
            rows.append([_('Shortages (Missing Stock)'), '', '', '', '', ''])
            merge_requests.append(_merge(SID, r() - 1, 0, 6))
            fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
                bold=True, font_size=14, font_color=COL_WHITE, bg_color=COL_RED)))
            rows.append([_('Product'), _('Location'), _('Lot/Serial'),
                         _('Theoretical'), _('Counted'), _('Shortage')])
            fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
                bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))
            for line in shortage_lines:
                rows.append([
                    line.product_id.display_name,
                    line.location_id.display_name,
                    line.lot_id.display_name if line.lot_id else '',
                    line.theoretical_qty, line.product_qty, line.difference_qty,
                ])
                row_i = r() - 1
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 0, 1, _cell_fmt(bold=True, bg_color=COL_LIGHT, borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 1, 3, _cell_fmt(borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 3, 5, _cell_fmt(h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 5, 6, _cell_fmt(bold=True, font_color=COL_RED, h_align='RIGHT', number_fmt='#,##0.00', borders=True)))

        # ── Surpluses section ─────────────────────────────────────────────────
        surplus_lines = inventory.line_ids.filtered(lambda l: l.difference_qty > 0)
        if surplus_lines:
            rows.append([''] * 6)
            rows.append([_('Surpluses (Extra Stock)'), '', '', '', '', ''])
            merge_requests.append(_merge(SID, r() - 1, 0, 6))
            fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
                bold=True, font_size=14, font_color=COL_WHITE, bg_color=COL_GREEN)))
            rows.append([_('Product'), _('Location'), _('Lot/Serial'),
                         _('Theoretical'), _('Counted'), _('Surplus')])
            fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
                bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))
            for line in surplus_lines:
                rows.append([
                    line.product_id.display_name,
                    line.location_id.display_name,
                    line.lot_id.display_name if line.lot_id else '',
                    line.theoretical_qty, line.product_qty, line.difference_qty,
                ])
                row_i = r() - 1
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 0, 1, _cell_fmt(bold=True, bg_color=COL_LIGHT, borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 1, 3, _cell_fmt(borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 3, 5, _cell_fmt(h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 5, 6, _cell_fmt(bold=True, font_color=COL_GREEN, h_align='RIGHT', number_fmt='#,##0.00', borders=True)))

        # ── Summary ───────────────────────────────────────────────────────────
        rows.append([''] * 6)
        rows.append([''] * 6)
        rows.append([_('Summary'), '', '', '', '', ''])
        merge_requests.append(_merge(SID, r() - 1, 0, 6))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 6, _cell_fmt(
            bold=True, font_size=14, font_color=COL_WHITE, bg_color=COL_DARK, h_align='CENTER')))
        rows.append([_('Total Lines:'), len(inventory.line_ids), _('Shortages:'), len(shortage_lines), '', ''])
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 1, _cell_fmt(bold=True, bg_color=COL_GREY, borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 1, 2, _cell_fmt(h_align='RIGHT', borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 2, 3, _cell_fmt(bold=True, bg_color=COL_GREY, borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 3, 4, _cell_fmt(h_align='RIGHT', borders=True)))
        rows.append([_('Surpluses:'), len(surplus_lines), '', '', '', ''])
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 0, 1, _cell_fmt(bold=True, bg_color=COL_GREY, borders=True)))
        fmt_requests.append(_repeat_cell(SID, r() - 1, r(), 1, 2, _cell_fmt(h_align='RIGHT', borders=True)))

        return rows, fmt_requests, merge_requests

    def _build_sheet2_data(self, inventory):
        """Return (rows_of_values, format_requests) for the Variance Report sheet."""
        _ = self.env._
        SID = 1
        rows = []
        fmt_requests = []

        rows.append([_('Product'), _('Location'), _('Theoretical'),
                     _('Counted'), _('Variance'), _('Recheck Qty')])
        fmt_requests.append(_repeat_cell(SID, 0, 1, 0, 6, _cell_fmt(
            bold=True, bg_color=COL_GREY, h_align='CENTER', borders=True)))

        variance_lines = inventory.line_ids.filtered(lambda l: l.difference_qty != 0)
        sorted_lines = sorted(
            variance_lines,
            key=lambda l: re.sub(r'\[.*?\]\s*', '', l.product_id.display_name or '').strip().lower()
        )

        for line in sorted_lines:
            clean_name = re.sub(r'\[.*?\]\s*', '', line.product_id.display_name or '').strip()
            diff = line.difference_qty
            rows.append([clean_name, line.location_id.display_name,
                         line.theoretical_qty, line.product_qty, diff, ''])
            row_i = len(rows) - 1
            fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 0, 1, _cell_fmt(bold=True, bg_color=COL_LIGHT, borders=True)))
            fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 1, 2, _cell_fmt(borders=True)))
            fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 2, 4, _cell_fmt(h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
            if diff < 0:
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 4, 5, _cell_fmt(bold=True, font_color=COL_RED, h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
            else:
                fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 4, 5, _cell_fmt(bold=True, font_color=COL_GREEN, h_align='RIGHT', number_fmt='#,##0.00', borders=True)))
            fmt_requests.append(_repeat_cell(SID, row_i, row_i + 1, 5, 6, _cell_fmt(borders=True)))

        return rows, fmt_requests

    def action_export_google_sheets(self):
        self.ensure_one()

        user_id = self.env.uid
        token = get_valid_token(self.env, user_id)

        if not token:
            auth_url = self._get_google_oauth_redirect_url(self.id)
            return {'type': 'ir.actions.act_url', 'url': auth_url, 'target': 'self'}

        ICP = self.env['ir.config_parameter'].sudo()
        folder_id   = ICP.get_param('retailit_stock_inventory_adjustment.google_sheets_folder_id') or None
        sheet_title = "Inventory Adjustment - %s" % self.name

        # ── Create spreadsheet ────────────────────────────────────────────────
        spreadsheet = _google_api(
            'POST', 'https://sheets.googleapis.com/v4/spreadsheets', token,
            body={
                'properties': {'title': sheet_title},
                'sheets': [
                    {'properties': {'title': 'Inventory',       'sheetId': 0}},
                    {'properties': {'title': 'Variance Report', 'sheetId': 1}},
                ],
            },
            params={'fields': 'spreadsheetId,spreadsheetUrl'},
        )
        sid = spreadsheet['spreadsheetId']
        spreadsheet_url = spreadsheet['spreadsheetUrl']

        # ── Build data ────────────────────────────────────────────────────────
        sheet1_rows, sheet1_fmt, sheet1_merge = self._build_sheet1_data(self)
        sheet2_rows, sheet2_fmt               = self._build_sheet2_data(self)

        def safe(v):
            return v if isinstance(v, (int, float)) else (str(v) if v is not None else '')

        s1_values = [[safe(c) for c in row] for row in sheet1_rows]
        s2_values = [[safe(c) for c in row] for row in sheet2_rows]

        # ── Write values ──────────────────────────────────────────────────────
        _google_api(
            'POST',
            'https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchUpdate' % sid,
            token,
            body={
                'valueInputOption': 'USER_ENTERED',
                'data': [
                    {'range': 'Inventory!A1',       'values': s1_values},
                    {'range': 'Variance Report!A1', 'values': s2_values},
                ],
            },
        )

        # ── Apply formatting ──────────────────────────────────────────────────
        format_requests = (
            sheet1_merge + sheet2_fmt + sheet1_fmt +
            [
                # Column widths — Inventory sheet (sheetId 0)
                _col_width(0, 0, 260),   # Product
                _col_width(0, 1, 150),   # Location
                _col_width(0, 2, 135),   # Lot/Serial
                _col_width(0, 3, 90),    # Theoretical
                _col_width(0, 4, 90),    # Counted
                _col_width(0, 5, 90),    # Difference
                # Column widths — Variance Report sheet (sheetId 1)
                _col_width(1, 0, 370),   # Product
                _col_width(1, 1, 185),   # Location
                _col_width(1, 2, 110),   # Theoretical
                _col_width(1, 3, 110),   # Counted
                _col_width(1, 4, 110),   # Variance
                _col_width(1, 5, 110),   # Recheck Qty
                # Freeze header row on Variance Report
                _freeze(1, rows=1),
                # Autofilter on Variance Report
                {
                    'setBasicFilter': {
                        'filter': {
                            'range': {
                                'sheetId': 1,
                                'startRowIndex': 0,
                                'endRowIndex': len(sheet2_rows),
                                'startColumnIndex': 0,
                                'endColumnIndex': 6,
                            }
                        }
                    }
                },
            ]
        )

        _google_api(
            'POST',
            'https://sheets.googleapis.com/v4/spreadsheets/%s:batchUpdate' % sid,
            token,
            body={'requests': format_requests},
        )

        # ── Move to folder if configured ──────────────────────────────────────
        if folder_id:
            try:
                file_meta = _google_api(
                    'GET',
                    'https://www.googleapis.com/drive/v3/files/%s' % sid,
                    token, params={'fields': 'parents'},
                )
                previous_parents = ','.join(file_meta.get('parents', []))
                _google_api(
                    'PATCH',
                    'https://www.googleapis.com/drive/v3/files/%s' % sid,
                    token, body={},
                    params={'addParents': folder_id, 'removeParents': previous_parents,
                            'fields': 'id,parents'},
                )
            except Exception:
                _logger.warning("Could not move sheet to folder %s", folder_id, exc_info=True)

        return {'type': 'ir.actions.act_url', 'url': spreadsheet_url, 'target': 'new'}

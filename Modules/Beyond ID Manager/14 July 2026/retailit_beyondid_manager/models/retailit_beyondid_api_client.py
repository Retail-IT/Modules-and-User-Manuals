import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, models
from odoo.exceptions import UserError


class BeyondIdAuthorizationError(UserError):
    pass


class BeyondIdTransientError(UserError):
    pass


class BeyondIdApiClient(models.AbstractModel):
    _name = "retailit.beyondid.api.client"
    _description = "Beyond ID API Client"

    PARAM_ENABLED = "retailit_beyondid_manager.enabled"
    PARAM_BASE_URL = "retailit_beyondid_manager.base_url"
    PARAM_USERNAME = "retailit_beyondid_manager.username"
    PARAM_PASSWORD = "retailit_beyondid_manager.password"
    PARAM_CLIENT_ID = "retailit_beyondid_manager.oauth_client_id"
    PARAM_WORKSPACE_TOKEN = "retailit_beyondid_manager.workspace_token"
    PARAM_ACCESS_TOKEN = "retailit_beyondid_manager.access_token"
    PARAM_ACCESS_TOKEN_EXPIRES_AT = "retailit_beyondid_manager.access_token_expires_at"

    DEFAULT_BASE_URL = "https://beyondid.keonn.com"
    DEFAULT_CLIENT_ID = "cloud"
    TIMEOUT = 30
    TOKEN_EXPIRY_BUFFER = 30

    def _get_param(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(key, default)

    def _get_config(self):
        base_url = (self._get_param(self.PARAM_BASE_URL, self.DEFAULT_BASE_URL) or "").strip()
        return {
            "enabled": self._get_param(self.PARAM_ENABLED, "False") == "True",
            "base_url": base_url.rstrip("/"),
            "username": (self._get_param(self.PARAM_USERNAME) or "").strip(),
            "password": self._get_param(self.PARAM_PASSWORD) or "",
            "client_id": (self._get_param(self.PARAM_CLIENT_ID, self.DEFAULT_CLIENT_ID) or "").strip(),
            "workspace_token": (self._get_param(self.PARAM_WORKSPACE_TOKEN) or "").strip(),
        }

    def _validate_required_config(self, config=None, require_workspace=False):
        config = config or self._get_config()
        missing = []
        for key, label in [
            ("base_url", _("Base URL")),
            ("username", _("Username")),
            ("password", _("Password")),
            ("client_id", _("OAuth Client ID")),
        ]:
            if not config.get(key):
                missing.append(label)
        if require_workspace and not config.get("workspace_token"):
            missing.append(_("Workspace/App Token"))
        if missing:
            raise UserError(_("Please configure the following Beyond ID fields: %s") % ", ".join(missing))
        return config

    def _build_url(self, path, config=None):
        config = config or self._get_config()
        return "%s/%s" % (config["base_url"].rstrip("/"), path.lstrip("/"))

    def _post_form(self, path, values, headers=None, config=None):
        config = config or self._get_config()
        data = urllib.parse.urlencode(values).encode()
        request_headers = {
            "User-Agent": "Odoo Beyond ID Manager",
            "Accept": "application/json,text/plain,*/*",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self._build_url(path, config=config),
            data=data,
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:
                body = response.read().decode("utf-8", "replace")
                content_type = response.headers.get("content-type", "")
                return response.status, content_type, body
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            if error.code in (401, 403):
                raise BeyondIdAuthorizationError(
                    _("Beyond ID authorization failed with HTTP %(status)s: %(body)s")
                    % {
                        "status": error.code,
                        "body": body[:500] or error.reason,
                    }
                ) from error
            if error.code in (408, 429, 500, 502, 503, 504):
                raise BeyondIdTransientError(_("Beyond ID returned temporary HTTP %(status)s: %(body)s") % {
                    "status": error.code,
                    "body": body[:500] or error.reason,
                }) from error
            raise UserError(_("Beyond ID returned HTTP %(status)s: %(body)s") % {
                "status": error.code,
                "body": body[:500] or error.reason,
            }) from error
        except (TimeoutError, socket.timeout) as error:
            raise BeyondIdTransientError(
                _("Connection timed out while waiting for Beyond ID. Please try again later. Details: %s")
                % error
            ) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise BeyondIdTransientError(
                    _("Connection timed out while waiting for Beyond ID. Please try again later. Details: %s")
                    % error.reason
                ) from error
            raise UserError(
                _("Connection failed. Odoo could not reach Beyond ID. Please verify the Base URL and network access. Details: %s")
                % error.reason
            ) from error

    def _parse_json(self, body):
        try:
            return json.loads(body or "{}")
        except ValueError as error:
            raise UserError(_("Connection failed. Beyond ID returned an invalid JSON response.")) from error

    def _clear_token_cache(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(self.PARAM_ACCESS_TOKEN, "")
        params.set_param(self.PARAM_ACCESS_TOKEN_EXPIRES_AT, "")

    def _get_cached_access_token(self):
        params = self.env["ir.config_parameter"].sudo()
        access_token = params.get_param(self.PARAM_ACCESS_TOKEN)
        expires_at = params.get_param(self.PARAM_ACCESS_TOKEN_EXPIRES_AT)
        if not access_token or not expires_at:
            return False
        try:
            if time.time() < float(expires_at):
                return access_token
        except ValueError:
            pass
        self._clear_token_cache()
        return False

    def _set_cached_access_token(self, access_token, token_data):
        expires_in = token_data.get("expires_in")
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 0
        if expires_in <= self.TOKEN_EXPIRY_BUFFER:
            return
        expires_at = time.time() + expires_in - self.TOKEN_EXPIRY_BUFFER
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(self.PARAM_ACCESS_TOKEN, access_token)
        params.set_param(self.PARAM_ACCESS_TOKEN_EXPIRES_AT, str(expires_at))

    def get_access_token(self, force_refresh=False, config=None):
        if not force_refresh:
            cached_access_token = self._get_cached_access_token()
            if cached_access_token:
                return cached_access_token, {"cached": True}
        config = self._validate_required_config(config=config)
        _status, _content_type, body = self._post_form(
            "/advancloud/oauth/token",
            {
                "grant_type": "password",
                "username": config["username"],
                "password": config["password"],
                "client_id": config["client_id"],
            },
            config=config,
        )
        data = self._parse_json(body)
        access_token = data.get("access_token")
        if not access_token:
            raise UserError(
                _("Connection failed. Beyond ID did not return an OAuth access token. Please verify the username, password and OAuth Client ID.")
            )
        self._set_cached_access_token(access_token, data)
        return access_token, data

    def test_connection(self):
        config = self._validate_required_config()
        access_token, token_data = self.get_access_token(force_refresh=True)
        workspace_token = config.get("workspace_token")
        if workspace_token:
            _status, _content_type, body = self._post_form(
                "/advancloud/import/stock/status",
                {
                    "token": workspace_token,
                    "reporttype": "json",
                },
                headers={"Authorization": "Bearer %s" % access_token},
                config=config,
            )
            status_data = self._parse_json(body)
            if status_data.get("result") != "OK":
                error_message = (
                    status_data.get("exceptionmessage")
                    or status_data.get("message")
                    or status_data.get("code")
                    or body[:500]
                )
                raise UserError(
                    _(
                        "Connection failed. OAuth authentication is valid, but the Workspace/App Token was rejected by Beyond ID. "
                        "Please verify the Workspace/App Token. Details: %s"
                    )
                    % error_message
                )
        return {
            "expires_in": token_data.get("expires_in"),
            "workspace_checked": bool(workspace_token),
            "workspace_token": workspace_token,
        }

    def _get_authorized_context(self, config=None):
        config = self._validate_required_config(config=config, require_workspace=True)
        access_token, _token_data = self.get_access_token(config=config)
        return config, {"Authorization": "Bearer %s" % access_token}

    def list_shops(self):
        config, headers = self._get_authorized_context()
        _status, _content_type, body = self._post_form(
            "/advancloud/import/stock/status",
            {
                "token": config["workspace_token"],
                "reporttype": "json",
            },
            headers=headers,
            config=config,
        )
        data = self._parse_json(body)
        if data.get("result") != "OK":
            raise UserError(_("Could not load Beyond ID shops: %s") % body[:500])
        return data.get("shops") or []

    def _check_stock_response(self, data, shop_code=None, inventory_code=None):
        if data.get("result") == "KO":
            message = data.get("exceptionmessage") or data.get("message")
            if not message or message == "No inventory found":
                target = inventory_code or shop_code
                raise UserError(
                    _("No Beyond ID inventory/count is currently available for %s. Please select another inventory or confirm that the RFID count was uploaded to Beyond ID.")
                    % target
                )
            raise UserError(_("Beyond ID rejected the inventory request: %s") % message)
        return data

    def search_inventories(self, shop_code, inventory_type="UPLOAD", start_date=None, end_date=None):
        if not shop_code:
            raise UserError(_("Please select a Beyond ID shop."))
        config, headers = self._get_authorized_context()
        values = {
            "token": config["workspace_token"],
            "shop": shop_code,
            "reporttype": "json",
        }
        if inventory_type:
            values["type"] = inventory_type
        if start_date:
            values["startDate"] = start_date
        if end_date:
            values["endDate"] = end_date
        _status, _content_type, body = self._post_form(
            "/advancloud/import/stock/search",
            values,
            headers=headers,
            config=config,
        )
        data = self._check_stock_response(self._parse_json(body), shop_code=shop_code)
        return data.get("inventories") or []

    def download_inventory_by_code(self, shop_code, inventory_code, inventory_type="UPLOAD", mode="sku"):
        if not shop_code:
            raise UserError(_("Please select a Beyond ID shop."))
        if not inventory_code:
            raise UserError(_("Please select a Beyond ID inventory."))
        config, headers = self._get_authorized_context()
        values = {
            "token": config["workspace_token"],
            "shop": shop_code,
            "reporttype": "json",
            "mode": mode or "sku",
        }
        if inventory_type:
            values["type"] = inventory_type
        _status, content_type, body = self._post_form(
            "/advancloud/import/stock/download/%s" % urllib.parse.quote(str(inventory_code), safe=""),
            values,
            headers=headers,
            config=config,
        )
        if "json" not in (content_type or "").lower():
            raise UserError(_("Beyond ID did not return JSON stock data for inventory %s.") % inventory_code)
        return self._check_stock_response(
            self._parse_json(body),
            shop_code=shop_code,
            inventory_code=inventory_code,
        )

    def download_stock(self, shop_code, inventory_type="UPLOAD", mode="sku"):
        if not shop_code:
            raise UserError(_("Please select a Beyond ID shop."))
        config, headers = self._get_authorized_context()
        _status, content_type, body = self._post_form(
            "/advancloud/import/stock/download",
            {
                "token": config["workspace_token"],
                "shop": shop_code,
                "reporttype": "json",
                "type": inventory_type,
                "mode": mode or "sku",
            },
            headers=headers,
            config=config,
        )
        if "json" not in (content_type or "").lower():
            raise UserError(_("Beyond ID did not return JSON stock data for shop %s.") % shop_code)
        return self._check_stock_response(self._parse_json(body), shop_code=shop_code)

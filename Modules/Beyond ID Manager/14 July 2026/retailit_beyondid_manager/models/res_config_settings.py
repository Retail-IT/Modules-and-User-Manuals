from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    beyondid_enabled = fields.Boolean(
        string="Enable Beyond ID Integration",
        config_parameter="retailit_beyondid_manager.enabled",
    )
    beyondid_base_url = fields.Char(
        string="Base URL",
        default="https://beyondid.keonn.com",
        config_parameter="retailit_beyondid_manager.base_url",
        help="Base Beyond ID domain, for example https://beyondid.keonn.com.",
    )
    beyondid_username = fields.Char(
        string="Username",
        config_parameter="retailit_beyondid_manager.username",
    )
    beyondid_password = fields.Char(
        string="Password",
        config_parameter="retailit_beyondid_manager.password",
    )
    beyondid_oauth_client_id = fields.Char(
        string="OAuth Client ID",
        default="cloud",
        config_parameter="retailit_beyondid_manager.oauth_client_id",
        help="The AdvanCloud OAuth client ID. The API documentation uses 'cloud'.",
    )
    beyondid_workspace_token = fields.Char(
        string="Workspace/App Token",
        config_parameter="retailit_beyondid_manager.workspace_token",
        help="Technical workspace/app code required by Beyond ID API calls.",
    )

    def _beyondid_normalize_config_value(self, value):
        if isinstance(value, bool):
            return "True" if value else "False"
        return (value or "").strip()

    def _beyondid_has_unsaved_changes(self):
        self.ensure_one()
        client = self.env["retailit.beyondid.api.client"]
        saved_config = client._get_config()
        current_config = {
            "enabled": self.beyondid_enabled,
            "base_url": (self.beyondid_base_url or "").rstrip("/"),
            "username": self.beyondid_username,
            "password": self.beyondid_password,
            "client_id": self.beyondid_oauth_client_id,
            "workspace_token": self.beyondid_workspace_token,
        }
        for key, current_value in current_config.items():
            saved_value = saved_config.get(key)
            if key == "base_url":
                saved_value = (saved_value or "").rstrip("/")
            if self._beyondid_normalize_config_value(current_value) != self._beyondid_normalize_config_value(saved_value):
                return True
        return False

    def _beyondid_notification(self, message, notification_type="success", sticky=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Beyond ID"),
                "message": message,
                "type": notification_type,
                "sticky": sticky,
            },
        }

    def set_values(self):
        result = super().set_values()
        self.env["retailit.beyondid.api.client"]._clear_token_cache()
        return result

    def action_beyondid_test_connection(self):
        self.ensure_one()
        if self._beyondid_has_unsaved_changes():
            return self._beyondid_notification(
                _(
                    "Please save the Beyond ID settings before testing the connection. "
                    "The connection test uses the saved configuration."
                ),
                notification_type="warning",
                sticky=True,
            )
        result = self.env["retailit.beyondid.api.client"].test_connection()
        message = _("Connection successful. OAuth authentication is valid.")
        if result.get("workspace_checked"):
            message = _(
                'Connection successful. OAuth authentication is valid and Workspace/App Token "%s" was verified successfully.'
            ) % result.get("workspace_token")
        elif result.get("expires_in"):
            message = _("Connection successful. OAuth authentication is valid. Access token expires in %s seconds.") % result["expires_in"]
        return self._beyondid_notification(message)

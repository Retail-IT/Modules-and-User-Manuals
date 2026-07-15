from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBeyondIdSettings(TransactionCase):

    def setUp(self):
        super().setUp()
        self.settings = self.env["res.config.settings"].create({
            "beyondid_enabled": True,
            "beyondid_base_url": "https://beyondid.keonn.com/",
            "beyondid_username": "wilder",
            "beyondid_password": "secret",
            "beyondid_oauth_client_id": "cloud",
            "beyondid_workspace_token": "house_of_gol",
        })
        self.settings.execute()
        self.client = self.env["retailit.beyondid.api.client"]

    def test_config_is_persisted_for_client(self):
        config = self.client._get_config()

        self.assertTrue(config["enabled"])
        self.assertEqual(config["base_url"], "https://beyondid.keonn.com")
        self.assertEqual(config["username"], "wilder")
        self.assertEqual(config["password"], "secret")
        self.assertEqual(config["client_id"], "cloud")
        self.assertEqual(config["workspace_token"], "house_of_gol")

    def test_missing_required_config_raises_clear_error(self):
        self.env["ir.config_parameter"].sudo().set_param("retailit_beyondid_manager.username", "")

        with self.assertRaisesRegex(UserError, "Username"):
            self.client._validate_required_config()

    def test_test_connection_validates_oauth_and_workspace(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (200, "application/json", '{"result": "OK", "app": "house_of_gol", "shops": []}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses) as post_form:
            result = self.client.test_connection()

        self.assertTrue(result["workspace_checked"])
        self.assertEqual(result["workspace_token"], "house_of_gol")
        self.assertEqual(post_form.call_count, 2)
        oauth_call = post_form.call_args_list[0]
        self.assertEqual(oauth_call.args[0], "/advancloud/oauth/token")
        self.assertEqual(oauth_call.args[1]["client_id"], "cloud")
        workspace_call = post_form.call_args_list[1]
        self.assertEqual(workspace_call.args[0], "/advancloud/import/stock/status")
        self.assertEqual(workspace_call.args[1]["token"], "house_of_gol")

    def test_access_token_is_cached_for_api_calls(self):
        responses = [
            (200, "application/json", '{"access_token": "cached-token", "token_type": "bearer", "expires_in": 3600}'),
            (200, "application/json", '{"result": "OK", "app": "house_of_gol", "shops": [{"code": "Ballito"}]}'),
            (200, "application/json", '{"result": "OK", "app": "house_of_gol", "shops": [{"code": "Hillcrest"}]}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses) as post_form:
            first_shops = self.client.list_shops()
            second_shops = self.client.list_shops()

        self.assertEqual(first_shops, [{"code": "Ballito"}])
        self.assertEqual(second_shops, [{"code": "Hillcrest"}])
        self.assertEqual(post_form.call_count, 3)
        self.assertEqual(post_form.call_args_list[0].args[0], "/advancloud/oauth/token")
        self.assertEqual(post_form.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer cached-token")
        self.assertEqual(post_form.call_args_list[2].kwargs["headers"]["Authorization"], "Bearer cached-token")

    def test_settings_save_clears_access_token_cache(self):
        self.env["ir.config_parameter"].sudo().set_param("retailit_beyondid_manager.access_token", "old-token")
        self.env["ir.config_parameter"].sudo().set_param("retailit_beyondid_manager.access_token_expires_at", "9999999999")

        self.settings.execute()

        self.assertFalse(self.env["ir.config_parameter"].sudo().get_param("retailit_beyondid_manager.access_token"))
        self.assertFalse(self.env["ir.config_parameter"].sudo().get_param("retailit_beyondid_manager.access_token_expires_at"))

    def test_test_connection_rejects_invalid_workspace(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer"}'),
            (200, "application/json", '{"result": "KO", "code": "BADTOKEN"}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses):
            with self.assertRaisesRegex(UserError, "Workspace/App Token"):
                self.client.test_connection()

    def test_settings_button_requires_saved_values(self):
        self.settings.beyondid_username = "changed-but-not-saved"

        action = self.settings.action_beyondid_test_connection()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Please save", action["params"]["message"])

    def test_settings_button_returns_descriptive_success(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (200, "application/json", '{"result": "OK", "app": "house_of_gol", "shops": []}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses):
            action = self.settings.action_beyondid_test_connection()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")
        self.assertIn("OAuth authentication is valid", action["params"]["message"])
        self.assertIn("house_of_gol", action["params"]["message"])

    def test_list_shops_returns_beyondid_shops(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (200, "application/json", '{"result": "OK", "app": "house_of_gol", "shops": [{"code": "Ballito"}]}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses) as post_form:
            shops = self.client.list_shops()

        self.assertEqual(shops, [{"code": "Ballito"}])
        self.assertEqual(post_form.call_count, 2)
        self.assertEqual(post_form.call_args_list[1].args[0], "/advancloud/import/stock/status")

    def test_download_stock_returns_payload_for_shop(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (200, "application/json", '{"result": "OK", "data": [{"code": "123", "count": 2}]}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses) as post_form:
            payload = self.client.download_stock("Ballito")

        self.assertEqual(payload["data"], [{"code": "123", "count": 2}])
        self.assertEqual(post_form.call_args_list[1].args[0], "/advancloud/import/stock/download")
        self.assertEqual(post_form.call_args_list[1].args[1]["shop"], "Ballito")
        self.assertEqual(post_form.call_args_list[1].args[1]["mode"], "sku")
        self.assertEqual(post_form.call_args_list[1].args[1]["type"], "UPLOAD")

    def test_search_inventories_returns_available_counts(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (
                200,
                "application/json",
                '{"result": "OK", "inventories": [{"code": "INV-001", "numberOfEans": 724}]}',
            ),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses) as post_form:
            inventories = self.client.search_inventories("Hillcrest")

        self.assertEqual(inventories, [{"code": "INV-001", "numberOfEans": 724}])
        self.assertEqual(post_form.call_args_list[1].args[0], "/advancloud/import/stock/search")
        self.assertEqual(post_form.call_args_list[1].args[1]["shop"], "Hillcrest")
        self.assertEqual(post_form.call_args_list[1].args[1]["type"], "UPLOAD")

    def test_download_inventory_by_code_uses_sku_mode(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (
                200,
                "application/json",
                '{"properties": {"code": "INV-001"}, "data": [{"code": "123", "count": 2}]}',
            ),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses) as post_form:
            payload = self.client.download_inventory_by_code("Hillcrest", "INV-001")

        self.assertEqual(payload["properties"]["code"], "INV-001")
        self.assertEqual(payload["data"], [{"code": "123", "count": 2}])
        self.assertEqual(post_form.call_args_list[1].args[0], "/advancloud/import/stock/download/INV-001")
        self.assertEqual(post_form.call_args_list[1].args[1]["shop"], "Hillcrest")
        self.assertEqual(post_form.call_args_list[1].args[1]["mode"], "sku")
        self.assertEqual(post_form.call_args_list[1].args[1]["type"], "UPLOAD")

    def test_download_stock_rejects_ko_payload(self):
        responses = [
            (200, "application/json", '{"access_token": "token", "token_type": "bearer", "expires_in": 3600}'),
            (200, "application/json", '{"result": "KO", "exceptionmessage": "No inventory found"}'),
        ]

        with patch.object(type(self.client), "_post_form", side_effect=responses):
            with self.assertRaisesRegex(UserError, "No Beyond ID inventory/count"):
                self.client.download_stock("Ballito")

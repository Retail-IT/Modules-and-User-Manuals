from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockInventoryCloudImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Inventory = cls.env["retailit.stock.inventory"]
        cls.Product = cls.env["product.product"]
        cls.Location = cls.env["stock.location"]
        cls.Quant = cls.env["stock.quant"]
        cls.Wizard = cls.env["retailit.stock.inventory.cloud.import.wizard"]
        cls.Client = cls.env["retailit.beyondid.api.client"]

    def _create_location(self, name):
        return self.Location.create({
            "name": name,
            "usage": "internal",
        })

    def _create_product(self, name, barcode):
        return self.Product.create({
            "name": name,
            "barcode": barcode,
            "default_code": barcode,
            "is_storable": True,
        })

    def _set_stock(self, product, location, quantity):
        self.Quant._update_available_quantity(product, location, quantity)

    def _create_inventory(self, location, count_mode="cycle"):
        return self.Inventory.create({
            "name": "Cloud Import Test",
            "location_id": location.id,
            "count_mode": count_mode,
            "inventory_type": "manual",
        })

    def _create_wizard(self, inventory):
        return self.Wizard.create({
            "inventory_id": inventory.id,
            "shop_code": "Ballito",
        })

    def test_import_from_cloud_action_opens_wizard(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Action Location"))

        action = inventory.action_import_from_cloud()

        self.assertEqual(action["res_model"], "retailit.stock.inventory.cloud.import.wizard")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_inventory_id"], inventory.id)

    def test_import_from_cloud_is_draft_only(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Draft Only Location"))
        inventory.action_start()

        with self.assertRaisesRegex(UserError, "draft"):
            inventory.action_import_from_cloud()

    def test_wizard_shop_selection_loads_shops_from_beyondid(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Shop Location"))

        with patch.object(type(self.Client), "list_shops", return_value=[
            {"code": "Ballito", "name": "Ballito", "area": "KZN"},
            {"code": "Cornubia", "name": "Cornubia", "area": "KZN"},
        ]):
            defaults = self.Wizard.with_context(default_inventory_id=inventory.id).default_get([
                "inventory_id",
                "shop_code",
            ])
            options = self.Wizard._selection_shop_code()

        self.assertEqual(defaults["shop_code"], "Ballito")
        self.assertEqual(options, [
            ("Ballito", "Ballito - KZN"),
            ("Cornubia", "Cornubia - KZN"),
        ])

    def test_import_downloads_cloud_count_groups_duplicates_and_starts_inventory(self):
        location = self._create_location("QT Cloud Import Start Location")
        product = self._create_product("QT Cloud Import Start Product", "QT-CLOUD-START-IMPORT")
        self._set_stock(product, location, 10)
        inventory = self._create_inventory(location)
        wizard = self._create_wizard(inventory)
        payload = {
            "result": "OK",
            "code": "INV-001",
            "comment": "RFID count from handheld",
            "data": [
                {"code": "QT-CLOUD-START-IMPORT", "count": 4, "name": "QT Cloud Import Start Product"},
                {"code": "QT-CLOUD-START-IMPORT", "count": 2, "name": "QT Cloud Import Start Product"},
                {"code": "QT-CLOUD-MISSING", "count": 2, "name": "Missing Product"},
            ],
        }
        self.env["retailit.stock.inventory.cloud.count.line"].create({
            "wizard_id": wizard.id,
            "code": "INV-001",
            "shop_code": "Ballito",
            "inventory_type": "UPLOAD",
            "number_of_eans": 2,
            "is_selected": True,
        })

        with patch.object(type(self.Client), "download_inventory_by_code", return_value=payload) as download, \
             patch.object(type(self.Inventory), "_update_theoretical_qty", side_effect=AssertionError("Cloud import should not recalculate theoretical quantities")):
            action = wizard.action_import()

        download.assert_called_once()
        self.assertEqual(download.call_args.args[0], "Ballito")
        self.assertEqual(download.call_args.args[1], "INV-001")
        self.assertEqual(download.call_args.kwargs["mode"], "sku")
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["next"]["res_model"], "retailit.stock.inventory")
        self.assertEqual(action["params"]["next"]["res_id"], inventory.id)
        self.assertEqual(action["params"]["next"]["views"], [(False, "form")])
        self.assertEqual(wizard.note, "RFID count from handheld")
        self.assertEqual(wizard.raw_reference, "INV-001")
        self.assertEqual(inventory.state, "in_progress")
        self.assertEqual(inventory.inventory_type, "cloud")
        line = inventory.line_ids.filtered(lambda item: item.product_id == product)
        self.assertEqual(line.product_qty, 6)
        self.assertEqual(line.theoretical_qty, 10)
        self.assertEqual(line.count_source, "rfid")
        self.assertTrue(line.is_counted)
        self.assertEqual(inventory.missing_barcode_ids.barcode, "QT-CLOUD-MISSING")
        self.assertEqual(inventory.missing_barcode_ids.qty, 2)

    def test_import_creates_inventory_lines_and_missing_barcodes(self):
        location = self._create_location("QT Cloud Import Location")
        product = self._create_product("QT Cloud Import Product", "QT-CLOUD-IMPORT")
        self._set_stock(product, location, 10)
        inventory = self._create_inventory(location)
        wizard = self._create_wizard(inventory)
        payload = {
            "result": "OK",
            "data": [
                {"code": "QT-CLOUD-IMPORT", "count": 4, "name": "QT Cloud Import Product"},
                {"code": "QT-CLOUD-IMPORT", "count": 6, "name": "QT Cloud Import Product"},
                {"code": "QT-CLOUD-UNKNOWN", "count": 3, "name": "Unknown Product"},
            ],
        }
        self.env["retailit.stock.inventory.cloud.count.line"].create({
            "wizard_id": wizard.id,
            "code": "INV-IMPORT",
            "shop_code": "Ballito",
            "inventory_type": "UPLOAD",
            "number_of_eans": 3,
            "is_selected": True,
        })

        with patch.object(type(self.Client), "download_inventory_by_code", return_value=payload):
            action = wizard.action_import()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(inventory.inventory_type, "cloud")
        self.assertEqual(inventory.state, "in_progress")
        self.assertEqual(inventory.line_ids.product_id, product)
        self.assertEqual(inventory.line_ids.product_qty, 10)
        self.assertEqual(inventory.line_ids.theoretical_qty, 10)
        self.assertEqual(inventory.line_ids.count_source, "rfid")
        self.assertTrue(inventory.line_ids.is_counted)
        self.assertEqual(inventory.missing_barcode_ids.barcode, "QT-CLOUD-UNKNOWN")
        self.assertEqual(inventory.missing_barcode_ids.qty, 3)

    def test_start_cloud_inventory_requires_imported_data(self):
        inventory = self.Inventory.create({
            "name": "Empty Cloud Import Test",
            "location_id": self._create_location("QT Empty Cloud Location").id,
            "count_mode": "cycle",
            "inventory_type": "cloud",
        })

        with self.assertRaisesRegex(UserError, "import inventory data"):
            inventory.action_start()

    def test_start_cloud_inventory_after_import_keeps_cloud_lines(self):
        location = self._create_location("QT Cloud Start Location")
        product = self._create_product("QT Cloud Start Product", "QT-CLOUD-START")
        self._set_stock(product, location, 7)
        inventory = self._create_inventory(location, count_mode="full")
        wizard = self._create_wizard(inventory)
        payload = {
            "result": "OK",
            "data": [
                {"code": "QT-CLOUD-START", "count": 5, "name": "QT Cloud Start Product"},
            ],
        }
        self.env["retailit.stock.inventory.cloud.count.line"].create({
            "wizard_id": wizard.id,
            "code": "INV-START",
            "shop_code": "Ballito",
            "inventory_type": "UPLOAD",
            "number_of_eans": 1,
            "is_selected": True,
        })

        with patch.object(type(self.Client), "download_inventory_by_code", return_value=payload):
            wizard.action_import()

        self.assertEqual(inventory.state, "in_progress")
        self.assertEqual(inventory.line_ids.filtered(lambda line: line.product_id == product).product_qty, 5)
        self.assertEqual(inventory.line_ids.filtered(lambda line: line.product_id == product).count_source, "rfid")

    def test_search_inventories_lists_counts_and_auto_selects_single_count(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Search Location"))
        wizard = self._create_wizard(inventory)

        with patch.object(type(self.Client), "search_inventories", return_value=[{
            "code": "INV-HILLCREST",
            "reference": "Morning RFID count",
            "shop": "Hillcrest",
            "type": "UPLOAD",
            "timestamp": 1782302856686,
            "numberOfEans": 724,
            "numberOfEpcs": 0,
        }]) as search:
            wizard.action_search_inventories()

        search.assert_called_once_with("Ballito", inventory_type="UPLOAD")
        self.assertEqual(wizard.count_line_count, 1)
        self.assertEqual(wizard.selected_count_id.code, "INV-HILLCREST")
        self.assertTrue(wizard.selected_count_id.is_selected)
        self.assertEqual(wizard.selected_count_id.reference, "Morning RFID count")
        self.assertEqual(wizard.selected_count_id.number_of_eans, 724)

    def test_selecting_one_inventory_count_unselects_the_previous_count(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Single Select Location"))
        wizard = self._create_wizard(inventory)
        first = self.env["retailit.stock.inventory.cloud.count.line"].create({
            "wizard_id": wizard.id,
            "code": "INV-FIRST",
            "shop_code": "Ballito",
            "inventory_type": "UPLOAD",
            "number_of_eans": 10,
            "is_selected": True,
        })
        second = self.env["retailit.stock.inventory.cloud.count.line"].create({
            "wizard_id": wizard.id,
            "code": "INV-SECOND",
            "shop_code": "Ballito",
            "inventory_type": "UPLOAD",
            "number_of_eans": 20,
        })

        second.is_selected = True

        self.assertFalse(first.is_selected)
        self.assertTrue(second.is_selected)
        self.assertEqual(wizard.selected_count_id, second)

    def test_import_requires_selected_inventory_count(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Selected Count Location"))
        wizard = self._create_wizard(inventory)

        with self.assertRaisesRegex(UserError, "select a Beyond ID inventory count"):
            wizard.action_import()

    def test_import_rejects_empty_cloud_payload(self):
        inventory = self._create_inventory(self._create_location("QT Cloud Empty Payload Location"))
        wizard = self._create_wizard(inventory)
        self.env["retailit.stock.inventory.cloud.count.line"].create({
            "wizard_id": wizard.id,
            "code": "INV-EMPTY",
            "shop_code": "Ballito",
            "inventory_type": "UPLOAD",
            "number_of_eans": 0,
            "is_selected": True,
        })

        with patch.object(type(self.Client), "download_inventory_by_code", return_value={"result": "OK", "data": []}):
            with self.assertRaisesRegex(UserError, "importable barcode rows"):
                wizard.action_import()

        self.assertEqual(inventory.state, "draft")
        self.assertFalse(inventory.line_ids)

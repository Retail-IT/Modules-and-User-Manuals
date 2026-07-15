import base64
import io

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockInventoryCountModes(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Inventory = cls.env["retailit.stock.inventory"]
        cls.Product = cls.env["product.product"]
        cls.Location = cls.env["stock.location"]
        cls.Quant = cls.env["stock.quant"]

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

    def _rfid_file(self, rows):
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Any Sheet Name"
        sheet.append(["code", "count", "name"])
        for row in rows:
            sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        workbook.close()
        return base64.b64encode(stream.getvalue())

    def _create_import_inventory(self, location, count_mode, rows):
        return self.Inventory.create({
            "name": "Count Mode Test",
            "location_id": location.id,
            "count_mode": count_mode,
            "inventory_type": "import",
            "import_file": self._rfid_file(rows),
            "import_filename": "RFID.xlsx",
        })

    def test_full_count_import_reports_unimported_stock_as_missed(self):
        location = self._create_location("QT Full Count Location")
        counted_product = self._create_product("QT Counted Product", "QT-FULL-COUNTED")
        missed_product = self._create_product("QT Missed Product", "QT-FULL-MISSED")
        self._set_stock(counted_product, location, 5)
        self._set_stock(missed_product, location, 8)

        inventory = self._create_import_inventory(
            location,
            "full",
            [["QT-FULL-COUNTED", 3, "QT Counted Product"]],
        )
        inventory.action_start()

        self.assertEqual(inventory.state, "in_progress")
        counted_line = inventory.line_ids.filtered(lambda line: line.product_id == counted_product)
        missed_shortage_line = inventory.line_ids.filtered(lambda line: line.product_id == missed_product)
        self.assertEqual(counted_line.product_qty, 3)
        self.assertEqual(counted_line.count_source, "rfid")
        self.assertTrue(counted_line.is_counted)
        self.assertEqual(inventory.missed_item_ids.product_id, missed_product)
        self.assertEqual(inventory.missed_item_ids.theoretical_qty, 8)
        self.assertEqual(missed_shortage_line.product_qty, 0)
        self.assertEqual(missed_shortage_line.theoretical_qty, 8)
        self.assertEqual(missed_shortage_line.difference_qty, -8)
        self.assertEqual(missed_shortage_line.count_source, "system")
        self.assertFalse(missed_shortage_line.is_counted)
        self.assertIn(missed_shortage_line, inventory.shortage_ids)
        self.assertEqual(set(inventory.shortage_ids.product_id.ids), {counted_product.id, missed_product.id})
        self.assertEqual(inventory.shortage_count, 2)

    def test_cycle_count_import_does_not_report_unimported_stock_as_missed(self):
        location = self._create_location("QT Cycle Count Location")
        counted_product = self._create_product("QT Cycle Counted Product", "QT-CYCLE-COUNTED")
        ignored_product = self._create_product("QT Cycle Ignored Product", "QT-CYCLE-IGNORED")
        self._set_stock(counted_product, location, 5)
        self._set_stock(ignored_product, location, 8)

        inventory = self._create_import_inventory(
            location,
            "cycle",
            [["QT-CYCLE-COUNTED", 3, "QT Cycle Counted Product"]],
        )
        inventory.action_start()

        self.assertEqual(inventory.line_ids.product_id, counted_product)
        self.assertFalse(inventory.missed_item_ids)
        self.assertEqual(inventory.shortage_ids.product_id, counted_product)
        self.assertNotIn(ignored_product, inventory.shortage_ids.product_id)

    def test_full_count_manual_qty_edit_counts_missed_shortage_line(self):
        location = self._create_location("QT Full Manual Edit Location")
        counted_product = self._create_product("QT Full Manual Edit Counted", "QT-FULL-MANUAL-COUNTED")
        missed_product = self._create_product("QT Full Manual Edit Missed", "QT-FULL-MANUAL-MISSED")
        self._set_stock(counted_product, location, 5)
        self._set_stock(missed_product, location, 8)

        inventory = self._create_import_inventory(
            location,
            "full",
            [["QT-FULL-MANUAL-COUNTED", 5, "QT Full Manual Edit Counted"]],
        )
        inventory.action_start()
        missed_line = inventory.line_ids.filtered(lambda line: line.product_id == missed_product)

        self.assertEqual(missed_line.product_qty, 0)
        self.assertEqual(missed_line.count_source, "system")
        self.assertFalse(missed_line.is_counted)
        self.assertEqual(inventory.missed_item_ids.product_id, missed_product)

        missed_line.write({"product_qty": 6})
        inventory.action_refresh_missed_items()
        missed_line.invalidate_recordset()
        inventory.invalidate_recordset(["line_ids", "missed_item_ids"])

        self.assertEqual(missed_line.product_qty, 6)
        self.assertEqual(missed_line.count_source, "manual")
        self.assertTrue(missed_line.is_counted)
        self.assertFalse(inventory.missed_item_ids.filtered(lambda item: item.product_id == missed_product))
        self.assertIn(missed_line, inventory.shortage_ids)
        self.assertEqual(missed_line.difference_qty, -2)
        self.assertEqual(inventory.line_count, 2)

    def test_cycle_count_manual_qty_edit_marks_line_counted_and_persists(self):
        location = self._create_location("QT Cycle Manual Edit Location")
        product = self._create_product("QT Cycle Manual Edit Product", "QT-CYCLE-MANUAL-EDIT")
        self._set_stock(product, location, 10)

        inventory = self._create_import_inventory(
            location,
            "cycle",
            [["QT-CYCLE-MANUAL-EDIT", 3, "QT Cycle Manual Edit Product"]],
        )
        inventory.action_start()
        line = inventory.line_ids.filtered(lambda item: item.product_id == product)

        line.write({"is_counted": False, "count_source": "system"})
        line.write({"product_qty": 12})
        inventory.action_refresh_missed_items()
        line.invalidate_recordset()

        self.assertEqual(line.product_qty, 12)
        self.assertEqual(line.count_source, "manual")
        self.assertTrue(line.is_counted)
        self.assertEqual(line.difference_qty, 2)
        self.assertIn(line, inventory.surplus_ids)
        self.assertFalse(inventory.missed_item_ids)

    def test_scan_counted_product_removes_it_from_full_count_missed_items(self):
        location = self._create_location("QT Full Scan Location")
        imported_product = self._create_product("QT Imported Product", "QT-FULL-IMPORTED")
        scanned_product = self._create_product("QT Scanned Product", "QT-FULL-SCANNED")
        self._set_stock(imported_product, location, 5)
        self._set_stock(scanned_product, location, 8)

        inventory = self._create_import_inventory(
            location,
            "full",
            [["QT-FULL-IMPORTED", 5, "QT Imported Product"]],
        )
        inventory.action_start()
        self.assertEqual(inventory.missed_item_ids.product_id, scanned_product)

        lookup = inventory.action_scan_barcode("QT-FULL-SCANNED")
        self.assertEqual(lookup["match_source"], "inventory_item")
        result = inventory.action_apply_scan_qty("QT-FULL-SCANNED", 4)

        self.assertEqual(result["current_qty"], 4)
        self.assertFalse(inventory.missed_item_ids)
        scanned_line = inventory.line_ids.filtered(lambda line: line.product_id == scanned_product)
        self.assertEqual(scanned_line.product_qty, 4)
        self.assertEqual(scanned_line.count_source, "scan")
        self.assertTrue(scanned_line.is_counted)
        self.assertTrue(scanned_line.was_scanned)
        first_line = self.env["retailit.stock.inventory.line"].search([("inventory_id", "=", inventory.id)], limit=1)
        self.assertEqual(first_line.product_id, scanned_product)

    def test_scan_quantity_cannot_be_negative(self):
        location = self._create_location("QT Scan Negative Location")
        product = self._create_product("QT Scan Negative Product", "QT-SCAN-NEGATIVE")
        self._set_stock(product, location, 5)

        inventory = self._create_import_inventory(
            location,
            "cycle",
            [["QT-SCAN-NEGATIVE", 2, "QT Scan Negative Product"]],
        )
        inventory.action_start()

        with self.assertRaises(UserError):
            inventory.action_apply_scan_qty("QT-SCAN-NEGATIVE", -1)

        self.assertEqual(inventory.line_ids.product_qty, 2)

    def test_scan_unknown_barcode_creates_missing_barcode(self):
        location = self._create_location("QT Missing Scan Location")
        inventory = self.Inventory.create({
            "name": "Missing Scan Test",
            "location_id": location.id,
            "count_mode": "cycle",
            "inventory_type": "manual",
        })
        inventory.action_start()

        result = inventory.action_scan_barcode("QT-SCAN-MISSING")

        self.assertEqual(result["match_source"], "missing")
        self.assertEqual(inventory.missing_barcode_ids.barcode, "QT-SCAN-MISSING")
        self.assertEqual(inventory.missing_barcode_count, 1)
        action = inventory.action_view_missing_barcodes()
        self.assertEqual(action["res_model"], "retailit.stock.inventory.missing.barcode")
        self.assertEqual(action["domain"], [("inventory_id", "=", inventory.id)])

    def test_scan_rejects_invalid_text_payload_as_barcode(self):
        location = self._create_location("QT Invalid Scan Location")
        inventory = self.Inventory.create({
            "name": "Invalid Scan Test",
            "location_id": location.id,
            "count_mode": "cycle",
            "inventory_type": "manual",
        })
        inventory.action_start()

        invalid_payload = (
            "Durante las pruebas del Full Count, eliminé una de las líneas del conteo "
            "y luego guardé el conteo."
        )
        with self.assertRaises(UserError):
            inventory.action_scan_barcode(invalid_payload)

        self.assertFalse(inventory.missing_barcode_ids)

    def test_full_count_deleted_line_becomes_missed_item(self):
        location = self._create_location("QT Full Delete Line Location")
        first_product = self._create_product("QT Full Delete Line Product 1", "QT-FULL-DELETE-LINE-1")
        second_product = self._create_product("QT Full Delete Line Product 2", "QT-FULL-DELETE-LINE-2")
        self._set_stock(first_product, location, 7)
        self._set_stock(second_product, location, 9)

        inventory = self._create_import_inventory(
            location,
            "full",
            [
                ["QT-FULL-DELETE-LINE-1", 7, "QT Full Delete Line Product 1"],
                ["QT-FULL-DELETE-LINE-2", 9, "QT Full Delete Line Product 2"],
            ],
        )
        inventory.action_start()
        self.assertFalse(inventory.missed_item_ids)

        inventory.line_ids.unlink()

        self.assertEqual(set(inventory.missed_item_ids.product_id.ids), {first_product.id, second_product.id})
        self.assertEqual(sum(inventory.missed_item_ids.mapped("theoretical_qty")), 16)
        self.assertEqual(set(inventory.shortage_ids.product_id.ids), {first_product.id, second_product.id})
        self.assertEqual(sum(inventory.shortage_ids.mapped("theoretical_qty")), 16)
        self.assertEqual(sum(inventory.shortage_ids.mapped("product_qty")), 0)
        self.assertFalse(inventory.missing_barcode_ids)
        self.assertEqual(inventory.line_count, 0)
        self.assertFalse(inventory.visible_line_ids)
        self.assertEqual(inventory.missed_item_count, 2)
        self.assertEqual(inventory.shortage_count, 2)
        action = inventory.action_view_lines()
        visible_lines = self.env["retailit.stock.inventory.line"].search(action["domain"])
        self.assertFalse(visible_lines)

    def test_full_count_parent_write_deleted_lines_become_missed_items(self):
        location = self._create_location("QT Full Parent Delete Location")
        first_product = self._create_product("QT Full Parent Delete Product 1", "QT-FULL-PARENT-DELETE-1")
        second_product = self._create_product("QT Full Parent Delete Product 2", "QT-FULL-PARENT-DELETE-2")
        self._set_stock(first_product, location, 4)
        self._set_stock(second_product, location, 6)

        inventory = self._create_import_inventory(
            location,
            "full",
            [
                ["QT-FULL-PARENT-DELETE-1", 4, "QT Full Parent Delete Product 1"],
                ["QT-FULL-PARENT-DELETE-2", 6, "QT Full Parent Delete Product 2"],
            ],
        )
        inventory.action_start()
        self.assertFalse(inventory.missed_item_ids)

        inventory.write({"line_ids": [(2, line.id, 0) for line in inventory.line_ids]})

        self.assertEqual(set(inventory.missed_item_ids.product_id.ids), {first_product.id, second_product.id})
        self.assertEqual(sum(inventory.missed_item_ids.mapped("theoretical_qty")), 10)
        self.assertEqual(set(inventory.shortage_ids.product_id.ids), {first_product.id, second_product.id})
        self.assertEqual(sum(inventory.shortage_ids.mapped("theoretical_qty")), 10)
        self.assertEqual(sum(inventory.shortage_ids.mapped("product_qty")), 0)
        self.assertFalse(inventory.missing_barcode_ids)
        self.assertEqual(inventory.line_count, 0)
        self.assertFalse(inventory.visible_line_ids)
        self.assertEqual(inventory.missed_item_count, 2)
        self.assertEqual(inventory.shortage_count, 2)
        action = inventory.action_view_lines()
        visible_lines = self.env["retailit.stock.inventory.line"].search(action["domain"])
        self.assertFalse(visible_lines)

    def test_full_count_deleted_import_line_without_quant_is_not_missed(self):
        location = self._create_location("QT Full Delete No Quant Location")
        product = self._create_product("QT Full Delete No Quant Product", "QT-FULL-DELETE-NO-QUANT")

        inventory = self._create_import_inventory(
            location,
            "full",
            [["QT-FULL-DELETE-NO-QUANT", 5, "QT Full Delete No Quant Product"]],
        )
        inventory.action_start()
        self.assertEqual(inventory.line_ids.product_id, product)
        self.assertFalse(inventory.missed_item_ids)

        inventory.line_ids.unlink()
        inventory.action_refresh_missed_items()

        self.assertFalse(inventory.missed_item_ids)
        self.assertFalse(inventory.shortage_ids)
        action = inventory.action_view_missed_items()
        self.assertEqual(action["res_model"], "retailit.stock.inventory.missed.item")
        self.assertEqual(action["domain"], [("inventory_id", "=", inventory.id)])
        self.assertEqual(inventory.missed_item_count, 0)

    def test_scanner_context_distinguishes_manual_and_auto_flows(self):
        location = self._create_location("QT Scanner Context Location")
        product = self._create_product("QT Scanner Context Product", "QT-SCANNER-CONTEXT")
        inventory = self._create_import_inventory(
            location,
            "cycle",
            [["QT-SCANNER-CONTEXT", 2, "QT Scanner Context Product"]],
        )
        inventory.action_start()

        manual_action = inventory.action_open_scan()
        self.assertTrue(manual_action["context"]["continuous_scan"])
        self.assertFalse(manual_action["context"]["default_barcode"])
        self.assertFalse(manual_action["context"]["default_scan_result"])

        auto_action = inventory.action_open_scan("QT-SCANNER-CONTEXT")
        self.assertFalse(auto_action["context"]["continuous_scan"])
        self.assertEqual(auto_action["context"]["default_barcode"], "QT-SCANNER-CONTEXT")
        self.assertEqual(auto_action["context"]["default_scan_result"]["product_id"], product.id)

    def test_full_count_validation_adjusts_uncounted_missed_shortages_to_zero(self):
        location = self._create_location("QT Full Validate Location")
        counted_product = self._create_product("QT Validate Counted Product", "QT-FULL-VALIDATE")
        missed_product = self._create_product("QT Validate Missed Product", "QT-FULL-VALIDATE-MISSED")
        self._set_stock(counted_product, location, 5)
        self._set_stock(missed_product, location, 8)

        inventory = self._create_import_inventory(
            location,
            "full",
            [["QT-FULL-VALIDATE", 5, "QT Validate Counted Product"]],
        )
        inventory.action_start()
        inventory.action_validate()

        self.assertEqual(inventory.state, "done")
        self.assertEqual(inventory.missed_item_ids.product_id, missed_product)
        missed_move = inventory.move_ids.filtered(lambda move: move.product_id == missed_product)
        self.assertEqual(missed_move.product_uom_qty, 8)

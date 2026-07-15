import csv
import io
import time
from unittest.mock import patch

from odoo.addons.retailit_beyondid_manager.models.retailit_beyondid_api_client import (
    BeyondIdAuthorizationError,
    BeyondIdTransientError,
)
from odoo.addons.retailit_beyondid_product_sync.models import product_product as product_product_module
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBeyondIdProductSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.params.set_param("retailit_beyondid_manager.enabled", "True")
        cls.params.set_param("retailit_beyondid_manager.base_url", "https://beyondid.example.test")
        cls.params.set_param("retailit_beyondid_manager.username", "tester")
        cls.params.set_param("retailit_beyondid_manager.password", "secret")
        cls.params.set_param("retailit_beyondid_manager.oauth_client_id", "cloud")
        cls.params.set_param("retailit_beyondid_manager.workspace_token", "houseofgolfpre")
        cls.params.set_param("retailit_beyondid_manager.access_token", "test-token")
        cls.params.set_param("retailit_beyondid_manager.access_token_expires_at", str(time.time() + 3600))
        cls.params.set_param("retailit_beyondid_product_sync.batch_size", "2")
        cls.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "False")

    def _create_product(self, name="Beyond Test Product", barcode="0123456789012", price=12.5):
        template = self.env["product.template"].create({
            "name": name,
            "list_price": price,
        })
        product = template.product_variant_id
        product.write({
            "barcode": barcode,
            "default_code": "BT-%s" % product.id,
        })
        return product

    def _create_product_with_unique_barcode(self, name="Beyond Unique Test Product", price=12.5):
        product = self._create_product(name=name, barcode=False, price=price)
        product.write({
            "barcode": "88%011d" % product.id,
        })
        return product

    def _create_variant_product(self, barcode="0123456789018", price=10.0):
        attribute = self.env["product.attribute"].create({
            "name": "Beyond Size",
            "create_variant": "always",
        })
        value = self.env["product.attribute.value"].create({
            "name": "Medium",
            "attribute_id": attribute.id,
        })
        template = self.env["product.template"].create({
            "name": "Beyond Variant Product",
            "list_price": price,
            "attribute_line_ids": [Command.create({
                "attribute_id": attribute.id,
                "value_ids": [Command.set(value.ids)],
            })],
        })
        product = template.product_variant_id
        product.write({
            "barcode": barcode,
            "default_code": "BV-%s" % product.id,
        })
        return product

    def _execute_with_fake_api(self, products, responses=None, **kwargs):
        calls = []
        responses = list(responses or [{"result": "OK", "lines": []}])

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append({
                "operation": operation,
                "filename": filename,
                "csv": csv_content.decode("utf-8"),
                "authorized_context": authorized_context,
                "upload_options": upload_options or {},
            })
            if len(responses) > 1:
                return responses.pop(0)
            return responses[0]

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(products, **kwargs)
        return run, calls

    def _csv_rows(self, csv_content):
        return list(csv.DictReader(io.StringIO(csv_content)))

    def _line(self, product, level="INFO", code="OK", data="Accepted"):
        return {
            "level": level,
            "code": code,
            "line": "productid:%s skuid:%s" % (product.id, product.id),
            "data": data,
        }

    def test_import_maps_odoo_variant_id_and_preserves_barcode_text(self):
        product = self._create_product(barcode="0012345678901", price=44)

        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(run.total_sent, 1)
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertEqual(product.beyondid_external_productid, str(product.id))
        self.assertEqual(product.beyondid_external_skuid, str(product.id))
        self.assertEqual(product.beyondid_last_code, "0012345678901")
        self.assertEqual(calls[0]["operation"], "import")
        self.assertEqual(calls[0]["upload_options"], {})
        self.assertTrue(calls[0]["authorized_context"])
        rows = self._csv_rows(calls[0]["csv"])
        self.assertEqual(rows[0]["itemtype"], "sku")
        self.assertEqual(rows[0]["productid"], str(product.id))
        self.assertEqual(rows[0]["skuid"], str(product.id))
        self.assertEqual(rows[0]["code"], "0012345678901")
        self.assertEqual(rows[0]["price"], "44.00")
        self.assertIn(product.name, rows[0]["name"])
        self.assertNotIn("extra", rows[0])
        self.assertEqual(rows[0]["members"], "37.40")

    def test_default_api_batch_size_is_25_when_not_configured(self):
        self.params.set_param("retailit_beyondid_product_sync.batch_size", "")
        run = self.env["retailit.beyondid.product.sync.run"].new({})

        self.assertEqual(run._batch_size(), 25)

    def test_manual_sync_splits_80_products_into_25_row_api_batches(self):
        self.params.set_param("retailit_beyondid_product_sync.batch_size", "25")
        products = self.env["product.product"].browse()
        for index in range(80):
            products |= self._create_product(
                name="Beyond Batch Product %s" % index,
                barcode="99887766%05d" % index,
            )

        run, calls = self._execute_with_fake_api(products, execution_type="manual")
        batch_lengths = [len(self._csv_rows(call["csv"])) for call in calls]

        self.assertEqual(run.state, "done")
        self.assertEqual(run.api_calls, 4)
        self.assertEqual(batch_lengths, [25, 25, 25, 5])
        self.assertEqual(run.total_sent, 80)

    def test_sync_button_opens_progress_client_action(self):
        product = self._create_product(barcode="0012345678913")

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv") as upload:
            action = product.action_beyondid_sync_selected()

        self.assertFalse(upload.called)
        self.assertEqual(product.beyondid_sync_state, "pending")
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "retailit_beyondid_product_sync.progress")
        self.assertEqual(action["target"], "current")
        self.assertEqual(action["params"]["product_ids"], product.ids)

    def test_full_resync_reset_requires_confirmation(self):
        wizard = self.env["retailit.beyondid.product.full.resync.wizard"].create({})

        with self.assertRaises(UserError):
            wizard.action_reset_products()

    def test_full_resync_reset_marks_eligible_products_pending(self):
        product = self._create_product_with_unique_barcode(name="Full Reset Product")
        run, _calls = self._execute_with_fake_api(product, execution_type="manual")
        self.assertEqual(run.state, "done")
        self.assertEqual(product._beyondid_prepare_sync_item()["action"], "no_change")

        wizard = self.env["retailit.beyondid.product.full.resync.wizard"].create({
            "confirm_full_resync": True,
        })
        action = wizard.action_reset_products()

        product.invalidate_recordset()
        self.assertEqual(product.beyondid_sync_state, "pending")
        self.assertTrue(product.beyondid_needs_sync)
        self.assertFalse(product.beyondid_sync_reason)
        self.assertFalse(product.beyondid_last_error)
        self.assertFalse(product.beyondid_last_warning)
        self.assertFalse(product.beyondid_last_payload_hash)
        self.assertEqual(product._beyondid_prepare_sync_item()["action"], "import")
        self.assertEqual(action["tag"], "display_notification")

        reset_run = self.env["retailit.beyondid.product.sync.run"].search([
            ("execution_type", "=", "reset"),
        ], limit=1)
        self.assertEqual(reset_run.operation, "reset")
        self.assertEqual(reset_run.state, "done")
        self.assertGreaterEqual(reset_run.total_reset, 1)

    def test_selected_resync_reset_only_marks_selected_products_pending(self):
        selected_product = self._create_product_with_unique_barcode(name="Selected Reset Product")
        untouched_product = self._create_product_with_unique_barcode(name="Untouched Reset Product")
        run, _calls = self._execute_with_fake_api(
            selected_product | untouched_product,
            execution_type="manual",
        )
        self.assertEqual(run.state, "done")
        self.assertEqual(selected_product._beyondid_prepare_sync_item()["action"], "no_change")
        self.assertEqual(untouched_product._beyondid_prepare_sync_item()["action"], "no_change")

        wizard = self.env["retailit.beyondid.product.full.resync.wizard"].with_context(
            active_model="product.product",
            active_ids=selected_product.ids,
            reset_selected_only=True,
        ).create({
            "confirm_full_resync": True,
        })
        self.assertEqual(wizard.reset_scope, "selected")
        self.assertEqual(wizard.selected_product_count, 1)
        self.assertEqual(wizard.eligible_product_count, 1)
        action = wizard.action_reset_products()

        (selected_product | untouched_product).invalidate_recordset()
        self.assertEqual(selected_product.beyondid_sync_state, "pending")
        self.assertTrue(selected_product.beyondid_needs_sync)
        self.assertFalse(selected_product.beyondid_last_payload_hash)
        self.assertEqual(selected_product._beyondid_prepare_sync_item()["action"], "import")
        self.assertEqual(untouched_product.beyondid_sync_state, "synced")
        self.assertFalse(untouched_product.beyondid_needs_sync)
        self.assertEqual(untouched_product._beyondid_prepare_sync_item()["action"], "no_change")
        self.assertEqual(action["tag"], "display_notification")

    def test_full_resync_reset_does_not_reset_missing_barcode_product(self):
        product = self._create_product(name="Missing Barcode Reset", barcode=False)
        product.with_context(skip_beyondid_mark_pending=True).write({
            "beyondid_sync_state": "synced",
            "beyondid_needs_sync": False,
            "beyondid_last_payload_hash": "existing-hash",
            "beyondid_last_error": "keep this marker",
        })

        wizard = self.env["retailit.beyondid.product.full.resync.wizard"].create({
            "confirm_full_resync": True,
        })
        wizard.action_reset_products()

        product.invalidate_recordset()
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertEqual(product.beyondid_last_payload_hash, "existing-hash")
        self.assertEqual(product.beyondid_last_error, "keep this marker")

    def test_initial_load_prepare_creates_batches_and_skip_issues(self):
        valid_a = self._create_product_with_unique_barcode(name="Initial Load Valid A")
        valid_b = self._create_product_with_unique_barcode(name="Initial Load Valid B")
        missing_barcode = self._create_product(name="Initial Load Missing Barcode", barcode=False)
        load = self.env["retailit.beyondid.product.initial.load"].with_context(
            initial_load_product_ids=(valid_a | valid_b | missing_barcode).ids,
        ).create({
            "name": "Initial Load Prepare Test",
            "batch_size": 1,
        })

        action = load.action_prepare_load()

        self.assertEqual(load.state, "ready")
        self.assertEqual(load.total_products, 3)
        self.assertEqual(load.total_valid, 2)
        self.assertEqual(load.total_skipped, 1)
        self.assertEqual(load.total_batches, 2)
        self.assertEqual(load.batch_ids.mapped("product_count"), [1, 1])
        self.assertEqual(load.issue_ids.reason, "missing_barcode")
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["next"], {"type": "ir.actions.client", "tag": "soft_reload"})

    def test_initial_load_controls_are_locked_after_prepare(self):
        product = self._create_product_with_unique_barcode(name="Initial Load Locked Controls")
        load = self.env["retailit.beyondid.product.initial.load"].with_context(
            initial_load_product_ids=product.ids,
        ).create({
            "name": "Initial Load Locked Controls Test",
            "batch_size": 10,
        })

        load.write({"batch_size": 5})
        with self.assertRaisesRegex(UserError, "name cannot be changed"):
            load.write({"name": "Changed Initial Load Name"})

        load.action_prepare_load()

        with self.assertRaisesRegex(UserError, "Only draft initial product loads can be prepared"):
            load.action_prepare_load()
        with self.assertRaisesRegex(UserError, "batch size can only be changed"):
            load.write({"batch_size": 1})
        with self.assertRaisesRegex(UserError, "Only draft initial product loads can be deleted"):
            load.unlink()

    def test_initial_load_draft_can_be_deleted(self):
        load = self.env["retailit.beyondid.product.initial.load"].create({
            "name": "Initial Load Draft Delete Test",
        })
        load_id = load.id

        load.unlink()

        self.assertFalse(self.env["retailit.beyondid.product.initial.load"].browse(load_id).exists())

    def test_initial_load_processes_batch_with_verify_before_import(self):
        product = self._create_product_with_unique_barcode(name="Initial Load Product")
        load = self.env["retailit.beyondid.product.initial.load"].with_context(
            initial_load_product_ids=product.ids,
        ).create({
            "name": "Initial Load Process Test",
            "batch_size": 5000,
            "confirm_clean_environment": True,
        })
        load.action_prepare_load()
        prepared = self.env["retailit.beyondid.product.initial.load"].action_initial_load_progress_start(load.id)
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append(operation)
            if operation == "verify":
                return {"result": "OK", "totals": {"INFO": 1}, "lines": []}
            return {"result": "OK", "totals": {"INFO": 2}, "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            result = self.env["retailit.beyondid.product.initial.load"].action_initial_load_process_batch(
                load.id,
                prepared["batches"][0]["id"],
            )
            finalized = self.env["retailit.beyondid.product.initial.load"].action_initial_load_finalize(load.id)

        product.invalidate_recordset()
        self.assertEqual(calls, ["verify", "import"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(finalized["state"], "done")
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertEqual(product.beyondid_external_productid, str(product.id))
        self.assertEqual(load.total_imported, 1)

    def test_initial_load_import_error_marks_only_known_error_and_unconfirmed_rest(self):
        failed_product = self._create_product_with_unique_barcode(name="Initial Load Duplicate Product")
        unconfirmed_product = self._create_product_with_unique_barcode(name="Initial Load Unconfirmed Product")
        products = failed_product | unconfirmed_product
        load = self.env["retailit.beyondid.product.initial.load"].with_context(
            initial_load_product_ids=products.ids,
        ).create({
            "name": "Initial Load Error Test",
            "batch_size": 5000,
            "confirm_clean_environment": True,
        })
        load.action_prepare_load()
        prepared = self.env["retailit.beyondid.product.initial.load"].action_initial_load_progress_start(load.id)

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            if operation == "verify":
                return {"result": "OK", "totals": {"INFO": 1}, "lines": []}
            return {
                "result": "OK",
                "totals": {"ERROR": 1, "INFO": 1},
                "lines": [{
                    "level": "ERROR",
                    "code": "import_errorImporting",
                    "line": "productid:%s skuid:%s" % (failed_product.id, failed_product.id),
                    "data": "duplicate key value violates unique constraint appitem_app_productid_skuid",
                }],
            }

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            result = self.env["retailit.beyondid.product.initial.load"].action_initial_load_process_batch(
                load.id,
                prepared["batches"][0]["id"],
            )
            finalized = self.env["retailit.beyondid.product.initial.load"].action_initial_load_finalize(load.id)

        products.invalidate_recordset()
        self.assertEqual(result["status"], "warning")
        self.assertEqual(finalized["state"], "warning")
        self.assertEqual(failed_product.beyondid_sync_state, "failed")
        self.assertEqual(unconfirmed_product.beyondid_sync_state, "unconfirmed")
        self.assertTrue(failed_product.beyondid_needs_sync)
        self.assertTrue(unconfirmed_product.beyondid_needs_sync)
        self.assertEqual(load.total_failed, 1)
        self.assertEqual(load.total_unconfirmed, 1)
        self.assertEqual(load.issue_count, 2)

    def test_initial_load_quota_error_splits_batch_and_imports_available_capacity(self):
        products = (
            self._create_product_with_unique_barcode(name="Initial Load Quota A")
            | self._create_product_with_unique_barcode(name="Initial Load Quota B")
            | self._create_product_with_unique_barcode(name="Initial Load Quota C")
            | self._create_product_with_unique_barcode(name="Initial Load Quota D")
        )
        load = self.env["retailit.beyondid.product.initial.load"].with_context(
            initial_load_product_ids=products.ids,
        ).create({
            "name": "Initial Load Quota Split Test",
            "batch_size": 5000,
            "confirm_clean_environment": True,
        })
        load.action_prepare_load()
        prepared = self.env["retailit.beyondid.product.initial.load"].action_initial_load_progress_start(load.id)
        remaining_capacity = {"count": 3}
        import_sizes = []
        verify_sizes = []

        def rows_from_csv(csv_content):
            return list(csv.DictReader(io.StringIO(csv_content.decode("utf-8"))))

        def quota_error(rows):
            first = rows[0]
            return {
                "result": "OK",
                "totals": {"ERROR": 1, "INFO": 1},
                "lines": [{
                    "level": "ERROR",
                    "code": "import_QUOTAEXCEEDED",
                    "line": "productid:%s skuid:%s" % (first["productid"], first["skuid"]),
                    "data": None,
                }],
            }

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            rows = rows_from_csv(csv_content)
            if operation == "verify":
                verify_sizes.append(len(rows))
                if len(rows) > remaining_capacity["count"]:
                    return quota_error(rows)
                return {"result": "OK", "totals": {"INFO": 1}, "lines": []}
            import_sizes.append(len(rows))
            remaining_capacity["count"] -= len(rows)
            return {"result": "OK", "totals": {"INFO": len(rows) + 1}, "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            result = self.env["retailit.beyondid.product.initial.load"].action_initial_load_process_batch(
                load.id,
                prepared["batches"][0]["id"],
            )
            finalized = self.env["retailit.beyondid.product.initial.load"].action_initial_load_finalize(load.id)

        products.invalidate_recordset()
        self.assertEqual(result["status"], "warning")
        self.assertEqual(finalized["state"], "warning")
        self.assertEqual(load.total_imported, 3)
        self.assertEqual(load.total_failed, 1)
        self.assertEqual(import_sizes, [2, 1])
        self.assertIn(4, verify_sizes)
        self.assertIn(1, verify_sizes)
        self.assertEqual(len(products.filtered(lambda product: product.beyondid_sync_state == "synced")), 3)
        failed_products = products.filtered(lambda product: product.beyondid_sync_state == "failed")
        self.assertEqual(len(failed_products), 1)
        self.assertEqual(load.issue_ids.reason, "quota_exceeded")
        self.assertEqual(load.issue_ids.code, "import_QUOTAEXCEEDED")

    def test_initial_load_retry_clears_previous_batch_errors(self):
        product = self._create_product_with_unique_barcode(name="Initial Load Retry Quota")
        load = self.env["retailit.beyondid.product.initial.load"].with_context(
            initial_load_product_ids=product.ids,
        ).create({
            "name": "Initial Load Retry Cleanup Test",
            "batch_size": 5000,
            "confirm_clean_environment": True,
        })
        load.action_prepare_load()
        prepared = self.env["retailit.beyondid.product.initial.load"].action_initial_load_progress_start(load.id)

        def quota_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None):
            rows = list(csv.DictReader(io.StringIO(csv_content.decode("utf-8"))))
            first = rows[0]
            if operation == "verify":
                return {
                    "result": "OK",
                    "totals": {"ERROR": 1, "INFO": 1},
                    "lines": [{
                        "level": "ERROR",
                        "code": "import_QUOTAEXCEEDED",
                        "line": "productid:%s skuid:%s" % (first["productid"], first["skuid"]),
                        "data": None,
                    }],
                }
            return {"result": "OK", "totals": {"INFO": 1}, "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", quota_upload):
            self.env["retailit.beyondid.product.initial.load"].action_initial_load_process_batch(
                load.id,
                prepared["batches"][0]["id"],
            )
            self.env["retailit.beyondid.product.initial.load"].action_initial_load_finalize(load.id)

        self.assertEqual(load.issue_count, 1)
        self.assertEqual(load.issue_ids.reason, "quota_exceeded")

        prepared = self.env["retailit.beyondid.product.initial.load"].action_initial_load_progress_start(load.id)

        def ok_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None):
            return {"result": "OK", "totals": {"INFO": 1}, "lines": []}

        with patch.object(api_class, "upload_products_csv", ok_upload):
            result = self.env["retailit.beyondid.product.initial.load"].action_initial_load_process_batch(
                load.id,
                prepared["batches"][0]["id"],
            )
            finalized = self.env["retailit.beyondid.product.initial.load"].action_initial_load_finalize(load.id)

        product.invalidate_recordset()
        self.assertEqual(result["status"], "done")
        self.assertEqual(finalized["state"], "done")
        self.assertFalse(load.issue_ids.filtered(lambda issue: issue.level in ("warning", "error")))
        self.assertEqual(product.beyondid_sync_state, "synced")

    def test_progress_prepare_returns_api_batches_without_uploading(self):
        self.params.set_param("retailit_beyondid_product_sync.batch_size", "2")
        products = (
            self._create_product(name="Beyond Progress A", barcode="8812345600001")
            | self._create_product(name="Beyond Progress B", barcode="8812345600002")
            | self._create_product(name="Beyond Progress C", barcode="8812345600003")
        )

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv") as upload:
            prepared = self.env["retailit.beyondid.product.sync.run"].action_progress_prepare(products.ids)

        self.assertFalse(upload.called)
        self.assertEqual(prepared["total_evaluated"], 3)
        self.assertEqual(prepared["total_batches"], 2)
        self.assertEqual([batch["count"] for batch in prepared["batches"]], [2, 1])
        run = self.env["retailit.beyondid.product.sync.run"].browse(prepared["run_id"])
        self.assertEqual(run.state, "running")
        self.assertEqual(set(products.mapped("beyondid_sync_state")), {"pending"})

    def test_progress_process_batch_syncs_products_and_finalizes(self):
        product = self._create_product(barcode="8812345600004")
        prepared = self.env["retailit.beyondid.product.sync.run"].action_progress_prepare(product.ids)
        batch = prepared["batches"][0]
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append({
                "operation": operation,
                "csv": csv_content.decode("utf-8"),
                "upload_options": upload_options or {},
            })
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            processed = self.env["retailit.beyondid.product.sync.run"].action_progress_process_batch(
                prepared["run_id"],
                batch["product_ids"],
                batch["operation"],
                True,
                False,
                batch.get("upload_options"),
            )
            finalized = self.env["retailit.beyondid.product.sync.run"].action_progress_finalize(prepared["run_id"])

        self.assertEqual(processed["status"], "done")
        self.assertEqual(finalized["state"], "done")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["operation"], "import")
        self.assertEqual(calls[0]["upload_options"], {})
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertEqual(product.beyondid_external_productid, str(product.id))

    def test_progress_existing_product_update_uses_update_only_import(self):
        product = self._create_product(barcode="8812345600006")
        self._execute_with_fake_api(product, execution_type="manual")
        product.product_tmpl_id.write({"name": "Beyond Progress Update"})

        prepared = self.env["retailit.beyondid.product.sync.run"].action_progress_prepare(product.ids)
        batch = prepared["batches"][0]
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append({
                "operation": operation,
                "csv": csv_content.decode("utf-8"),
                "upload_options": upload_options or {},
            })
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            processed = self.env["retailit.beyondid.product.sync.run"].action_progress_process_batch(
                prepared["run_id"],
                batch["product_ids"],
                batch["operation"],
                True,
                False,
                batch.get("upload_options"),
            )

        self.assertEqual(processed["status"], "done")
        self.assertEqual(batch["upload_options"], {"updateonly": "true"})
        self.assertEqual(calls[0]["upload_options"], {"updateonly": "true"})
        self.assertEqual(product.beyondid_sync_state, "synced")

    def test_progress_update_only_missing_remote_product_falls_back_to_normal_import(self):
        product = self._create_product(barcode="8812345600007")
        self._execute_with_fake_api(product, execution_type="manual")
        product.product_tmpl_id.write({"name": "Beyond Progress Recreate"})
        prepared = self.env["retailit.beyondid.product.sync.run"].action_progress_prepare(product.ids)
        batch = prepared["batches"][0]
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append(upload_options or {})
            if upload_options == {"updateonly": "true"}:
                return {
                    "result": "OK",
                    "totals": {"WARNING": 1, "INFO": 1},
                    "lines": [{
                        "level": "WARNING",
                        "code": "import_updateItemNotFound",
                        "line": "productid:%s skuid:%s" % (product.id, product.id),
                        "data": "productid:%s skuid:%s" % (product.id, product.id),
                    }],
                }
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            processed = self.env["retailit.beyondid.product.sync.run"].action_progress_process_batch(
                prepared["run_id"],
                batch["product_ids"],
                batch["operation"],
                True,
                False,
                batch.get("upload_options"),
            )

        self.assertEqual(processed["status"], "done")
        self.assertEqual(processed["api_calls"], 2)
        self.assertEqual(calls, [{"updateonly": "true"}, {}])
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)

    def test_progress_transient_timeout_keeps_product_retryable_without_traceback(self):
        product = self._create_product(barcode="8812345600005")
        prepared = self.env["retailit.beyondid.product.sync.run"].action_progress_prepare(product.ids)
        batch = prepared["batches"][0]
        api_class = self.env.registry["retailit.beyondid.api.client"]

        with patch.object(
            api_class,
            "upload_products_csv",
            side_effect=BeyondIdTransientError("Connection timed out while waiting for Beyond ID."),
        ):
            processed = self.env["retailit.beyondid.product.sync.run"].action_progress_process_batch(
                prepared["run_id"],
                batch["product_ids"],
                batch["operation"],
                True,
                False,
            )

        self.assertEqual(processed["status"], "transient_error")
        self.assertEqual(processed["api_calls"], 1)
        self.assertEqual(product.beyondid_sync_state, "pending")
        self.assertEqual(product.beyondid_sync_reason, "api_error")
        self.assertTrue(product.beyondid_needs_sync)

        failed = self.env["retailit.beyondid.product.sync.run"].action_progress_mark_batch_failed(
            prepared["run_id"],
            batch["product_ids"],
            batch["operation"],
            "Connection timed out while waiting for Beyond ID.",
        )
        finalized = self.env["retailit.beyondid.product.sync.run"].action_progress_finalize(prepared["run_id"])

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(finalized["state"], "failed")
        self.assertEqual(product.beyondid_sync_state, "failed")
        self.assertEqual(product.beyondid_sync_reason, "api_error")
        self.assertTrue(product.beyondid_needs_sync)

    def test_missing_barcode_is_skipped_without_api_call(self):
        product = self._create_product(barcode=False)

        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertFalse(calls)
        self.assertEqual(run.total_skipped, 1)
        self.assertEqual(product.beyondid_sync_state, "skipped")
        self.assertEqual(product.beyondid_sync_reason, "missing_barcode")
        self.assertTrue(run.issue_ids)
        self.assertEqual(run.issue_ids.reason, "missing_barcode")

    def test_new_product_without_barcode_is_not_marked_pending(self):
        product = self._create_product(barcode=False)

        self.assertEqual(product.beyondid_sync_state, "skipped")
        self.assertEqual(product.beyondid_sync_reason, "missing_barcode")
        self.assertFalse(product.beyondid_needs_sync)

    def test_missing_barcode_cleanup_reclassifies_old_pending_products(self):
        product = self._create_product(barcode=False)
        product.with_context(skip_beyondid_mark_pending=True).write({
            "beyondid_sync_state": "pending",
            "beyondid_sync_reason": False,
            "beyondid_needs_sync": True,
        })

        self.env["product.product"]._beyondid_cleanup_unsendable_pending_products()
        product.invalidate_recordset()

        self.assertEqual(product.beyondid_sync_state, "skipped")
        self.assertEqual(product.beyondid_sync_reason, "missing_barcode")
        self.assertFalse(product.beyondid_needs_sync)

    def test_sync_selected_processes_product_after_missing_barcode_is_fixed(self):
        product = self._create_product(barcode=False)
        self._execute_with_fake_api(product, execution_type="manual")

        product.write({"barcode": "0012345678914"})
        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(calls[0]["operation"], "import")
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertEqual(product.beyondid_last_code, "0012345678914")

    def test_unchanged_synced_product_is_not_sent_again(self):
        product = self._create_product(barcode="0012345678902")

        first_run, first_calls = self._execute_with_fake_api(product, execution_type="manual")
        second_run, second_calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(first_run.state, "done")
        self.assertEqual(len(first_calls), 1)
        self.assertFalse(second_calls)
        self.assertEqual(second_run.total_no_changes, 1)
        self.assertEqual(product.beyondid_sync_state, "synced")

    def test_template_price_change_marks_synced_product_pending(self):
        product = self._create_product(barcode="0012345678908", price=20)
        self._execute_with_fake_api(product, execution_type="manual")

        product.product_tmpl_id.write({"list_price": 35})

        self.assertEqual(product.beyondid_sync_state, "pending")
        self.assertTrue(product.beyondid_needs_sync)
        run, calls = self._execute_with_fake_api(product, execution_type="manual")
        rows = self._csv_rows(calls[0]["csv"])
        self.assertEqual(run.state, "done")
        self.assertEqual(calls[0]["upload_options"], {"updateonly": "true"})
        self.assertEqual(rows[0]["price"], "35.00")
        self.assertNotIn("extra", rows[0])
        self.assertEqual(rows[0]["members"], "29.75")

    def test_synced_product_name_change_uses_update_only_import(self):
        product = self._create_product(barcode="0012345678930", price=20)
        self._execute_with_fake_api(product, execution_type="manual")

        product.product_tmpl_id.write({"name": "Beyond Updated Name"})
        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["operation"], "import")
        self.assertEqual(calls[0]["upload_options"], {"updateonly": "true"})
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)

    def test_update_only_missing_remote_product_falls_back_to_normal_import(self):
        product = self._create_product(barcode="0012345678933", price=20)
        self._execute_with_fake_api(product, execution_type="manual")
        product.product_tmpl_id.write({"name": "Beyond Recreated From Odoo"})
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append({
                "operation": operation,
                "csv": csv_content.decode("utf-8"),
                "upload_options": upload_options or {},
            })
            if upload_options == {"updateonly": "true"}:
                return {
                    "result": "OK",
                    "totals": {"WARNING": 1, "INFO": 1},
                    "lines": [{
                        "level": "WARNING",
                        "code": "import_updateItemNotFound",
                        "line": "productid:%s skuid:%s" % (product.id, product.id),
                        "data": "productid:%s skuid:%s" % (product.id, product.id),
                    }],
                }
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(run.api_calls, 2)
        self.assertEqual([call["upload_options"] for call in calls], [{"updateonly": "true"}, {}])
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertFalse(product.beyondid_last_error)

    def test_update_only_not_found_then_import_duplicate_retries_update(self):
        product = self._create_product(barcode="0012345678934", price=20)
        self._execute_with_fake_api(product, execution_type="manual")
        product.product_tmpl_id.write({"name": "Beyond Eventually Consistent Update"})
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append(upload_options or {})
            if len(calls) == 1:
                return {
                    "result": "OK",
                    "totals": {"WARNING": 1, "INFO": 1},
                    "lines": [{
                        "level": "WARNING",
                        "code": "import_updateItemNotFound",
                        "line": "productid:%s skuid:%s" % (product.id, product.id),
                        "data": "productid:%s skuid:%s" % (product.id, product.id),
                    }],
                }
            if len(calls) == 2:
                return {
                    "result": "OK",
                    "totals": {"ERROR": 1, "INFO": 1},
                    "lines": [{
                        "level": "ERROR",
                        "file": "odoo_products_import.csv",
                        "line": "-",
                        "code": "import_errorImporting",
                        "data": (
                            'ERROR: duplicate key value violates unique constraint "appitem_app_productid_skuid" '
                            "Detail: Key (app, productid, skuid)=(34073646, %s, %s) already exists."
                        ) % (product.id, product.id),
                    }],
                }
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(run.api_calls, 3)
        self.assertEqual(calls, [{"updateonly": "true"}, {}, {"updateonly": "true"}])
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertFalse(product.beyondid_last_error)

    def test_mixed_new_and_existing_products_are_sent_in_separate_import_modes(self):
        existing_product = self._create_product(barcode="0012345678931")
        self._execute_with_fake_api(existing_product, execution_type="manual")
        existing_product.product_tmpl_id.write({"name": "Beyond Existing Update"})
        new_product = self._create_product(barcode="0012345678932")

        run, calls = self._execute_with_fake_api(existing_product | new_product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(len(calls), 2)
        call_by_options = {
            tuple(sorted(call["upload_options"].items())): call
            for call in calls
        }
        self.assertIn(tuple(), call_by_options)
        self.assertIn((("updateonly", "true"),), call_by_options)
        self.assertIn(new_product.barcode, call_by_options[tuple()]["csv"])
        self.assertIn(existing_product.barcode, call_by_options[(("updateonly", "true"),)]["csv"])

    def test_barcode_removed_after_sync_sends_delete(self):
        product = self._create_product(barcode="0012345678909")
        self._execute_with_fake_api(product, execution_type="manual")

        product.write({"barcode": False})
        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(calls[0]["operation"], "delete")
        self.assertEqual(product.beyondid_sync_state, "skipped")
        self.assertEqual(product.beyondid_sync_reason, "missing_barcode")

    def test_variant_price_extra_change_marks_synced_product_pending(self):
        product = self._create_variant_product()
        self._execute_with_fake_api(product, execution_type="manual")

        product.product_template_attribute_value_ids.write({"price_extra": 5})

        self.assertEqual(product.beyondid_sync_state, "pending")
        self.assertTrue(product.beyondid_needs_sync)
        run, calls = self._execute_with_fake_api(product, execution_type="manual")
        rows = self._csv_rows(calls[0]["csv"])
        self.assertEqual(run.state, "done")
        self.assertEqual(rows[0]["price"], "15.00")
        self.assertNotIn("extra", rows[0])
        self.assertEqual(rows[0]["members"], "12.75")

    def test_api_warning_keeps_product_synced_with_warning_state(self):
        product = self._create_product(barcode="0012345678903")
        warning = {"result": "OK", "lines": [self._line(product, level="WARNING", code="WARN", data="Minor issue")]}

        run, _calls = self._execute_with_fake_api(product, responses=[warning], execution_type="manual")

        self.assertEqual(run.state, "warning")
        self.assertEqual(run.total_warnings, 1)
        self.assertEqual(product.beyondid_sync_state, "warning")
        self.assertEqual(product.beyondid_sync_reason, False)
        self.assertIn("Minor issue", product.beyondid_last_warning)
        self.assertEqual(run.issue_ids.level, "warning")

    def test_api_error_marks_product_failed_without_automatic_retry(self):
        product = self._create_product(barcode="0012345678904")
        error = {"result": "OK", "lines": [self._line(product, level="ERROR", code="ERR", data="Rejected")]}

        run, _calls = self._execute_with_fake_api(product, responses=[error], execution_type="manual")

        self.assertEqual(run.state, "failed")
        self.assertEqual(run.total_failed, 1)
        self.assertEqual(product.beyondid_sync_state, "failed")
        self.assertEqual(product.beyondid_sync_reason, "validation_error")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertIn("Rejected", product.beyondid_last_error)
        self.assertEqual(run.issue_ids.level, "error")

    def test_transient_timeout_is_retried_before_failing_batch(self):
        product = self._create_product(barcode="0012345678922")
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append(csv_content.decode("utf-8"))
            if len(calls) == 1:
                raise BeyondIdTransientError("Connection timed out while waiting for Beyond ID.")
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(run.api_calls, 2)
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)

    def test_repeated_transient_timeout_splits_large_batch(self):
        self.params.set_param("retailit_beyondid_product_sync.batch_size", "6")
        products = self.env["product.product"].browse()
        for index in range(6):
            products |= self._create_product(
                name="Beyond Timeout Split %s" % index,
                barcode="77123456%05d" % index,
            )
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            rows = self._csv_rows(csv_content.decode("utf-8"))
            calls.append(len(rows))
            if len(rows) == 6:
                raise BeyondIdTransientError("Connection timed out while waiting for Beyond ID.")
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(products, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(calls, [6, 6, 5, 1])
        self.assertEqual(run.api_calls, 4)
        self.assertEqual(run.total_sent, 6)
        self.assertEqual(set(products.mapped("beyondid_sync_state")), {"synced"})

    def test_transient_timeout_keeps_product_pending_for_retry(self):
        product = self._create_product(barcode="0012345678923")
        api_class = self.env.registry["retailit.beyondid.api.client"]

        with patch.object(
            api_class,
            "upload_products_csv",
            side_effect=BeyondIdTransientError("Connection timed out while waiting for Beyond ID."),
        ):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(product, execution_type="manual")

        self.assertEqual(run.state, "failed")
        self.assertEqual(run.api_calls, 2)
        self.assertEqual(product.beyondid_sync_state, "failed")
        self.assertEqual(product.beyondid_sync_reason, "api_error")
        self.assertTrue(product.beyondid_needs_sync)

    def test_upload_timeout_is_reported_as_user_error(self):
        api = self.env["retailit.beyondid.api.client"]

        with patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")):
            with self.assertRaisesRegex(UserError, "Connection timed out"):
                api.upload_products_csv(b"itemtype,productid,skuid,code,price,name\n")

    def test_auth_error_marks_products_failed_without_raising_traceback(self):
        product = self._create_product(barcode="0012345678919")
        api_class = self.env.registry["retailit.beyondid.api.client"]

        with patch.object(api_class, "_get_authorized_context", side_effect=UserError("Token request timed out")):
            run = self.env["retailit.beyondid.product.sync.run"].create_and_execute(product, execution_type="manual")

        self.assertEqual(run.state, "failed")
        self.assertEqual(run.total_failed, 1)
        self.assertEqual(product.beyondid_sync_state, "failed")
        self.assertEqual(product.beyondid_sync_reason, "api_error")
        self.assertTrue(product.beyondid_needs_sync)
        self.assertIn("Token request timed out", product.beyondid_last_error)

    def test_expired_token_during_upload_refreshes_and_retries_once(self):
        api = self.env["retailit.beyondid.api.client"]
        config = api._get_config()
        authorized_context = {
            "config": config,
            "headers": {"Authorization": "Bearer old-token"},
        }
        calls = []

        def fake_post_multipart(api_client, path, fields, files, headers=None, config=None):
            calls.append(headers["Authorization"])
            if len(calls) == 1:
                raise BeyondIdAuthorizationError("Expired token")
            return 200, "application/json", '{"result": "OK", "lines": []}'

        def fake_authorized_context(api_client, config=None):
            return config or api._get_config(), {"Authorization": "Bearer new-token"}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "_post_multipart", fake_post_multipart), \
                patch.object(api_class, "_get_authorized_context", fake_authorized_context):
            response = api.upload_products_csv(
                b"itemtype,productid,skuid,code,price,name\n",
                authorized_context=authorized_context,
            )

        self.assertEqual(response["result"], "OK")
        self.assertEqual(calls, ["Bearer old-token", "Bearer new-token"])
        self.assertEqual(authorized_context["headers"]["Authorization"], "Bearer new-token")

    def test_sync_selected_processes_failed_validation_product(self):
        product = self._create_product(barcode="0012345678915")
        error = {"result": "OK", "lines": [self._line(product, level="ERROR", code="ERR", data="Rejected")]}
        self._execute_with_fake_api(product, responses=[error], execution_type="manual")

        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(calls[0]["operation"], "import")
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)
        self.assertFalse(product.beyondid_last_error)

    def test_cron_does_not_retry_validation_failed_product_without_changes(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "True")
        product = self._create_product(barcode="0012345678924")
        self.env.cr.execute(
            """
            UPDATE product_product
               SET beyondid_needs_sync = false,
                   beyondid_sync_state = 'synced'
             WHERE id != %s
            """,
            [product.id],
        )
        self.env["product.product"].invalidate_model(["beyondid_needs_sync", "beyondid_sync_state"])
        error = {"result": "OK", "lines": [self._line(product, level="ERROR", code="ERR", data="Rejected")]}
        self._execute_with_fake_api(product, responses=[error], execution_type="manual")

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv") as upload:
            result = product._cron_beyondid_sync_pending()

        self.assertFalse(result)
        self.assertFalse(upload.called)
        self.assertEqual(product.beyondid_sync_state, "failed")
        self.assertEqual(product.beyondid_sync_reason, "validation_error")
        self.assertFalse(product.beyondid_needs_sync)

    def test_sync_run_records_timing_breakdown(self):
        product = self._create_product(barcode="0012345678916")

        run, calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertEqual(len(calls), 1)
        self.assertGreater(run.duration, 0.0)
        self.assertGreater(run.odoo_prepare_duration, 0.0)
        self.assertGreater(run.auth_duration, 0.0)
        self.assertGreater(run.api_duration, 0.0)
        self.assertGreater(run.odoo_apply_duration, 0.0)

    def test_duplicate_barcodes_are_detected_by_batch_without_api_call(self):
        product_a = self._create_product(name="Duplicate Beyond A", barcode="0012345678917")
        product_b = self._create_product(name="Duplicate Beyond B", barcode="0012345678918")
        products = product_a | product_b

        product_class = self.env.registry["product.product"]
        with patch.object(
            product_class,
            "_beyondid_duplicate_product_map",
            return_value={product_a.id: product_b.id, product_b.id: product_a.id},
        ):
            run, calls = self._execute_with_fake_api(products, execution_type="manual")

        self.assertEqual(run.state, "done")
        self.assertFalse(calls)
        self.assertEqual(run.total_skipped, 2)
        self.assertEqual(set(products.mapped("beyondid_sync_reason")), {"duplicate_barcode"})

    def test_sync_selected_blocks_large_manual_selection(self):
        products = self.env["product.product"].browse()
        for index in range(2):
            products |= self._create_product(
                name="Beyond Manual Limit %s" % index,
                barcode="99123456%05d" % index,
            )

        with patch.object(product_product_module, "MANUAL_SYNC_LIMIT", 1):
            with self.assertRaisesRegex(UserError, "Manual sync supports up to 1 products"):
                products.action_beyondid_sync_selected()

    def test_archived_product_is_ignored_and_unarchive_imports_again(self):
        product = self._create_product(barcode="0012345678905")

        first_run, first_calls = self._execute_with_fake_api(product, execution_type="manual")
        self.assertEqual(first_run.state, "done")

        product.with_context(active_test=False).write({"active": False})
        product.invalidate_recordset()
        self.assertEqual(product.with_context(active_test=False).beyondid_sync_state, "synced")
        self.assertFalse(product.with_context(active_test=False).beyondid_needs_sync)

        ignored_run, ignored_calls = self._execute_with_fake_api(
            product.with_context(active_test=False),
            execution_type="manual",
            responses=[{"result": "OK", "lines": []}],
        )

        self.assertEqual(ignored_run.state, "done")
        self.assertFalse(ignored_calls)
        self.assertEqual(ignored_run.total_skipped, 1)
        self.assertEqual(product.with_context(active_test=False).beyondid_sync_state, "skipped")
        self.assertEqual(product.with_context(active_test=False).beyondid_sync_reason, "inactive_product")

        product.with_context(active_test=False).write({"active": True})
        product.invalidate_recordset()
        self.assertEqual(product.beyondid_sync_state, "pending")
        import_run, import_calls = self._execute_with_fake_api(product, execution_type="manual")

        self.assertEqual(import_run.state, "done")
        self.assertEqual(import_calls[0]["operation"], "import")
        self.assertEqual(product.beyondid_sync_state, "synced")
        self.assertFalse(product.beyondid_needs_sync)

    def test_cron_ignores_archived_pending_products(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "True")
        product = self._create_product(barcode="0012345678906")
        self.env.cr.execute(
            """
            UPDATE product_product
               SET beyondid_needs_sync = false,
                   beyondid_sync_state = 'synced'
             WHERE id != %s
            """,
            [product.id],
        )
        product.with_context(active_test=False).write({"active": False})
        self.env.cr.execute(
            """
            UPDATE product_product
               SET beyondid_needs_sync = true,
                   beyondid_sync_state = 'pending'
             WHERE id = %s
            """,
            [product.id],
        )
        self.env["product.product"].invalidate_model([
            "active",
            "beyondid_needs_sync",
            "beyondid_sync_state",
        ])

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv") as upload:
            result = product._cron_beyondid_sync_pending()

        self.assertFalse(result)
        self.assertFalse(upload.called)

    def test_product_sync_action_excludes_archived_products(self):
        action = self.env.ref("retailit_beyondid_product_sync.retailit_action_beyondid_product_sync").read()[0]

        self.assertEqual(action["domain"], "[('active', '=', True), ('product_tmpl_id.active', '=', True)]")
        self.assertNotIn("active_test", action["context"])
        self.assertNotIn("search_default_needs_attention", action["context"])

    def test_product_sync_search_panel_only_uses_sync_status(self):
        view = self.env.ref("retailit_beyondid_product_sync.retailit_view_beyondid_product_sync_search")
        arch = view.arch_db

        self.assertIn('<searchpanel>', arch)
        self.assertIn('name="beyondid_sync_state"', arch)
        self.assertNotIn('name="beyondid_sync_reason" icon="fa-exclamation-circle"', arch)
        self.assertIn('name="api_error"', arch)
        self.assertIn('name="validation_error"', arch)

    def test_cron_does_not_run_when_product_auto_sync_is_disabled(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "False")
        product = self._create_product(barcode="0012345678910")

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv") as upload:
            result = product._cron_beyondid_sync_pending()

        self.assertFalse(result)
        self.assertFalse(upload.called)
        self.assertEqual(product.beyondid_sync_state, "pending")

    def test_cron_runs_limited_pending_products_when_product_auto_sync_is_enabled(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "True")
        self.params.set_param("retailit_beyondid_product_sync.cron_limit", "1")
        product_a = self._create_product(barcode="0012345678911")
        product_b = self._create_product(barcode="0012345678912")
        products = product_a | product_b
        self.env.cr.execute(
            """
            UPDATE product_product
               SET beyondid_needs_sync = false,
                   beyondid_sync_state = 'synced'
             WHERE id NOT IN %s
            """,
            [tuple(products.ids)],
        )
        self.env["product.product"].invalidate_model(["beyondid_needs_sync", "beyondid_sync_state"])

        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append({
                "operation": operation,
                "csv": csv_content.decode("utf-8"),
                "authorized_context": authorized_context,
            })
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            result = product_a._cron_beyondid_sync_pending()

        synced_products = products.filtered(lambda product: product.beyondid_sync_state == "synced")
        pending_products = products.filtered(lambda product: product.beyondid_sync_state == "pending")
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self._csv_rows(calls[0]["csv"])), 1)
        self.assertEqual(len(synced_products), 1)
        self.assertEqual(len(pending_products), 1)

    def test_cron_prioritizes_actionable_products_over_invalid_pending_records(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "True")
        self.params.set_param("retailit_beyondid_product_sync.cron_limit", "1")
        valid_product = self._create_product(barcode="0012345678925")
        invalid_product = self._create_product(name="Beyond Invalid Pending", barcode=False)
        products = valid_product | invalid_product
        self.env.cr.execute(
            """
            UPDATE product_product
               SET beyondid_needs_sync = false,
                   beyondid_sync_state = 'synced'
             WHERE id NOT IN %s
            """,
            [tuple(products.ids)],
        )
        self.env.cr.execute(
            """
            UPDATE product_product
               SET write_date = NOW()
             WHERE id = %s
            """,
            [invalid_product.id],
        )
        self.env["product.product"].invalidate_model([
            "beyondid_needs_sync",
            "beyondid_sync_state",
            "write_date",
        ])
        calls = []

        def fake_upload(api_client, csv_content, operation="import", filename=None, authorized_context=None, upload_options=None):
            calls.append(csv_content.decode("utf-8"))
            return {"result": "OK", "lines": []}

        api_class = self.env.registry["retailit.beyondid.api.client"]
        with patch.object(api_class, "upload_products_csv", fake_upload):
            result = valid_product._cron_beyondid_sync_pending()

        valid_product.invalidate_recordset()
        invalid_product.invalidate_recordset()
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertIn(valid_product.barcode, calls[0])
        self.assertEqual(valid_product.beyondid_sync_state, "synced")
        self.assertEqual(invalid_product.beyondid_sync_state, "skipped")
        self.assertEqual(invalid_product.beyondid_sync_reason, "missing_barcode")

    def test_cron_records_missing_configuration_without_blocking(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "True")
        self.params.set_param("retailit_beyondid_manager.workspace_token", "")
        product = self._create_product(barcode="0012345678920")

        result = product._cron_beyondid_sync_pending()
        run = self.env["retailit.beyondid.product.sync.run"].search([], limit=1)

        self.assertTrue(result)
        self.assertEqual(run.execution_type, "cron")
        self.assertEqual(run.state, "failed")
        self.assertIn("Workspace/App Token", run.error_message)

    def test_cron_records_unexpected_error_without_blocking(self):
        self.params.set_param("retailit_beyondid_product_sync.auto_sync_enabled", "True")
        product = self._create_product(barcode="0012345678921")
        product_class = self.env.registry["product.product"]

        with patch.object(product_class, "_beyondid_duplicate_product_map", side_effect=RuntimeError("Unexpected sync issue")):
            result = product._cron_beyondid_sync_pending()

        run = self.env["retailit.beyondid.product.sync.run"].search([], limit=1)
        self.assertTrue(result)
        self.assertEqual(run.execution_type, "cron")
        self.assertEqual(run.state, "failed")
        self.assertIn("Unexpected sync issue", run.error_message)

    def test_global_api_error_fails_run_without_marking_products_synced(self):
        product = self._create_product(barcode="0012345678907")
        response = {
            "result": "OK",
            "lines": [{"level": "ERROR", "code": "GLOBAL", "data": "File rejected"}],
        }

        run, _calls = self._execute_with_fake_api(product, responses=[response], execution_type="manual")

        self.assertEqual(run.state, "failed")
        self.assertEqual(run.total_failed, 1)
        self.assertEqual(product.beyondid_sync_state, "failed")
        self.assertTrue(product.beyondid_needs_sync)
        self.assertIn("GLOBAL", run.issue_ids.mapped("code"))

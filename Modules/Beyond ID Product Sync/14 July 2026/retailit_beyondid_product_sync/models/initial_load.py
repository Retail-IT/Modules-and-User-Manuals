import csv
import io
import re
import time

from odoo.addons.retailit_beyondid_manager.models.retailit_beyondid_api_client import BeyondIdTransientError
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


INITIAL_LOAD_BATCH_SIZE = 5000
QUOTA_EXCEEDED_CODE = "QUOTAEXCEEDED"
PRODUCT_LINE_RE = re.compile(r"productid:([^\s,'\")]+)\s+skuid:([^\s,'\")]+)", re.IGNORECASE)
SQL_PRODUCT_RE = re.compile(r"\('([^']+)'\),\s*\('([^']+)'\)", re.IGNORECASE)


class BeyondIdProductInitialLoad(models.Model):
    _name = "retailit.beyondid.product.initial.load"
    _description = "Beyond ID Initial Product Load"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: self._default_name())
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("running", "Running"),
            ("done", "Done"),
            ("warning", "Done with Issues"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        index=True,
    )
    confirm_clean_environment = fields.Boolean(
        string="I confirm Beyond ID is clean for this initial load",
        help="Initial Product Load only imports new products. It does not delete or overwrite existing Beyond ID products.",
    )
    batch_size = fields.Integer(default=INITIAL_LOAD_BATCH_SIZE, required=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    duration = fields.Float(string="Duration (s)", readonly=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    batch_ids = fields.One2many("retailit.beyondid.product.initial.load.batch", "load_id", readonly=True)
    issue_ids = fields.One2many("retailit.beyondid.product.initial.load.issue", "load_id", readonly=True)
    issue_count = fields.Integer(compute="_compute_issue_count")
    current_batch = fields.Integer(readonly=True)
    total_batches = fields.Integer(readonly=True)
    total_products = fields.Integer(readonly=True)
    total_valid = fields.Integer(readonly=True)
    total_skipped = fields.Integer(readonly=True)
    total_sent = fields.Integer(readonly=True)
    total_imported = fields.Integer(readonly=True)
    total_failed = fields.Integer(readonly=True)
    total_unconfirmed = fields.Integer(readonly=True)
    api_calls = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)

    @api.model
    def _default_name(self):
        return _("Beyond ID Initial Product Load %s") % fields.Datetime.now()

    def write(self, vals):
        if "name" in vals:
            new_name = vals.get("name")
            if any(load.name != new_name for load in self):
                raise UserError(_("The initial load name cannot be changed after creation."))
        if "batch_size" in vals:
            new_batch_size = vals.get("batch_size")
            locked_loads = self.filtered(
                lambda load: load.state != "draft" and load.batch_size != new_batch_size
            )
            if locked_loads:
                raise UserError(_("The batch size can only be changed while the initial load is in Draft."))
        return super().write(vals)

    def unlink(self):
        protected_loads = self.filtered(lambda load: load.state != "draft")
        if protected_loads:
            raise UserError(_("Only draft initial product loads can be deleted."))
        return super().unlink()

    @api.depends("issue_ids")
    def _compute_issue_count(self):
        grouped = self.env["retailit.beyondid.product.initial.load.issue"]._read_group(
            [("load_id", "in", self.ids)],
            ["load_id"],
            ["__count"],
        )
        counts = {load.id: count for load, count in grouped}
        for load in self:
            load.issue_count = counts.get(load.id, 0)

    def _progress_duration(self):
        self.ensure_one()
        if not self.started_at:
            return 0.0
        return (fields.Datetime.now() - self.started_at).total_seconds()

    def _progress_summary(self):
        self.ensure_one()
        return {
            "load_id": self.id,
            "state": self.state,
            "current_batch": self.current_batch or 0,
            "total_batches": self.total_batches or 0,
            "total_products": self.total_products or 0,
            "total_valid": self.total_valid or 0,
            "total_skipped": self.total_skipped or 0,
            "total_sent": self.total_sent or 0,
            "total_imported": self.total_imported or 0,
            "total_failed": self.total_failed or 0,
            "total_unconfirmed": self.total_unconfirmed or 0,
            "api_calls": self.api_calls or 0,
            "duration": self.duration or self._progress_duration(),
        }

    def _normalized_batch_size(self):
        self.ensure_one()
        try:
            batch_size = int(self.batch_size or INITIAL_LOAD_BATCH_SIZE)
        except (TypeError, ValueError):
            batch_size = INITIAL_LOAD_BATCH_SIZE
        return max(1, min(batch_size, INITIAL_LOAD_BATCH_SIZE))

    def _split_ids(self, product_ids, size):
        for index in range(0, len(product_ids), size):
            yield product_ids[index:index + size]

    def action_prepare_load(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only draft initial product loads can be prepared."))

        self.batch_ids.unlink()
        self.issue_ids.unlink()

        Product = self.env["product.product"].with_context(active_test=False)
        product_ids = self.env.context.get("initial_load_product_ids")
        if product_ids:
            products = Product.browse(product_ids).exists().filtered(
                lambda product: product.active and product.product_tmpl_id.active
            )
        else:
            products = Product.search([
                ("active", "=", True),
                ("product_tmpl_id.active", "=", True),
            ], order="id")
        duplicate_product_by_id = products._beyondid_duplicate_product_map()
        products = products.with_context(
            beyondid_duplicate_product_by_id=duplicate_product_by_id,
        )

        valid_product_ids = []
        issue_values = []
        for product in products:
            reason = self._product_skip_reason(product)
            if reason:
                issue_values.append(self._issue_values(
                    product=product,
                    level="skipped",
                    reason=reason,
                    message=self._reason_message(reason),
                ))
                continue
            valid_product_ids.append(product.id)

        if issue_values:
            self.env["retailit.beyondid.product.initial.load.issue"].create(issue_values)

        batch_size = self._normalized_batch_size()
        batch_values = []
        for sequence, batch_product_ids in enumerate(self._split_ids(valid_product_ids, batch_size), start=1):
            batch_values.append({
                "load_id": self.id,
                "sequence": sequence,
                "product_count": len(batch_product_ids),
                "product_ids": [Command.set(batch_product_ids)],
            })
        if batch_values:
            self.env["retailit.beyondid.product.initial.load.batch"].create(batch_values)

        self.write({
            "state": "ready",
            "batch_size": batch_size,
            "current_batch": 0,
            "total_batches": len(batch_values),
            "total_products": len(products),
            "total_valid": len(valid_product_ids),
            "total_skipped": len(issue_values),
            "total_sent": 0,
            "total_imported": 0,
            "total_failed": 0,
            "total_unconfirmed": 0,
            "api_calls": 0,
            "started_at": False,
            "finished_at": False,
            "duration": 0.0,
            "error_message": False,
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Initial Product Load Prepared"),
                "message": _("%(valid)s valid products were prepared in %(batches)s batches. %(skipped)s products were skipped.") % {
                    "valid": len(valid_product_ids),
                    "batches": len(batch_values),
                    "skipped": len(issue_values),
                },
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_open_progress(self):
        self.ensure_one()
        if self.state not in ("ready", "warning", "failed"):
            raise UserError(_("Prepare the initial product load before starting it."))
        if not self.confirm_clean_environment:
            raise UserError(_("Please confirm that Beyond ID is clean before starting the initial product load."))
        if not self.batch_ids:
            raise UserError(_("There are no valid products to import."))
        return {
            "type": "ir.actions.client",
            "tag": "retailit_beyondid_product_sync.initial_load_progress",
            "target": "current",
            "params": {
                "initial_load_id": self.id,
            },
        }

    def action_view_issues(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Initial Load Issues"),
            "res_model": "retailit.beyondid.product.initial.load.issue",
            "view_mode": "list,form",
            "domain": [("load_id", "=", self.id)],
            "target": "current",
        }

    def action_view_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Initial Load Batches"),
            "res_model": "retailit.beyondid.product.initial.load.batch",
            "view_mode": "list,form",
            "domain": [("load_id", "=", self.id)],
            "target": "current",
        }

    def _product_skip_reason(self, product):
        self.ensure_one()
        if not (product.barcode or "").strip():
            return "missing_barcode"
        if product._beyondid_duplicate_barcode_product():
            return "duplicate_barcode"
        if not product._beyondid_display_name():
            return "missing_name"
        return False

    def _reason_message(self, reason):
        messages = {
            "missing_barcode": _("The product was skipped because it has no barcode."),
            "duplicate_barcode": _("The product was skipped because another active variant uses the same barcode."),
            "missing_name": _("The product was skipped because it has no name."),
            "verify_error": _("Beyond ID rejected the verification step for this product batch."),
            "api_error": _("Beyond ID did not confirm the product import."),
            "quota_exceeded": _("Beyond ID product quota was exceeded."),
            "remote_duplicate": _("Beyond ID reported that the product already exists."),
            "unconfirmed": _("The product was part of a batch with API errors and was not individually confirmed."),
        }
        return messages.get(reason, reason or _("The product was skipped."))

    def _issue_values(self, product=False, batch=False, level="error", reason=False, code=False, message=False, raw_line=False):
        self.ensure_one()
        return {
            "load_id": self.id,
            "batch_id": batch.id if batch else False,
            "product_id": product.id if product else False,
            "level": level,
            "reason": reason,
            "code": code,
            "message": message or "-",
            "raw_line": raw_line,
        }

    @api.model
    def action_initial_load_progress_start(self, load_id):
        load = self.browse(load_id).exists()
        if not load:
            raise UserError(_("The initial product load no longer exists."))
        if not load.confirm_clean_environment:
            raise UserError(_("Please confirm that Beyond ID is clean before starting the initial product load."))
        if load.state not in ("ready", "warning", "failed"):
            raise UserError(_("Only prepared initial loads can be started."))

        client = self.env["retailit.beyondid.api.client"]
        config = client._get_config()
        if not config.get("enabled"):
            raise UserError(_("Enable the Beyond ID integration before importing products."))
        client._validate_required_config(config=config, require_workspace=True)

        pending_batches = load.batch_ids.filtered(lambda batch: batch.state in ("pending", "failed", "warning"))
        load.write({
            "state": "running",
            "started_at": fields.Datetime.now(),
            "finished_at": False,
            "duration": 0.0,
            "error_message": False,
            "current_batch": 0,
            "total_sent": 0,
            "total_imported": 0,
            "total_failed": 0,
            "total_unconfirmed": 0,
            "api_calls": 0,
        })
        pending_batches.write({
            "state": "pending",
            "message": False,
            "sent_count": 0,
            "imported_count": 0,
            "failed_count": 0,
            "unconfirmed_count": 0,
            "api_calls": 0,
            "duration": 0.0,
            "verify_response": False,
            "import_response": False,
        })
        return {
            **load._progress_summary(),
            "batches": pending_batches.sorted("sequence")._progress_values(),
        }

    @api.model
    def action_initial_load_progress_authenticate(self, load_id):
        load = self.browse(load_id).exists()
        if not load:
            raise UserError(_("The initial product load no longer exists."))
        started_at = time.monotonic()
        client = self.env["retailit.beyondid.api.client"]
        config = client._get_config()
        client._get_authorized_context(config=config)
        load.duration = load._progress_duration()
        return {
            **load._progress_summary(),
            "auth_duration": time.monotonic() - started_at,
        }

    @api.model
    def action_initial_load_process_batch(self, load_id, batch_id):
        load = self.browse(load_id).exists()
        batch = self.env["retailit.beyondid.product.initial.load.batch"].browse(batch_id).exists()
        if not load or not batch or batch.load_id != load:
            raise UserError(_("The initial product load batch no longer exists."))
        if load.state != "running":
            raise UserError(_("The initial product load is not running."))
        return load._process_batch(batch)

    def _process_batch(self, batch):
        self.ensure_one()
        batch.ensure_one()
        started_at = time.monotonic()
        self.current_batch = batch.sequence
        self._clear_batch_retry_issues(batch)
        batch.write({
            "state": "verifying",
            "message": False,
            "started_at": fields.Datetime.now(),
        })

        products = batch.product_ids.with_context(active_test=False).exists()
        items = self._items_from_products(products, batch)
        if not items:
            batch.write({
                "state": "done",
                "finished_at": fields.Datetime.now(),
                "duration": time.monotonic() - started_at,
                "message": _("No products needed to be imported in this batch."),
            })
            return {
                **self._progress_summary(),
                "batch": batch._progress_values()[0],
                "status": "empty",
                "message": batch.message,
            }

        products = self.env["product.product"].concat(*(item["product"] for item in items))
        products.sudo().with_context(skip_beyondid_mark_pending=True).write({
            "beyondid_sync_state": "processing",
            "beyondid_last_sync_attempt_date": fields.Datetime.now(),
            "beyondid_last_operation": "import",
            "beyondid_last_error": False,
        })

        result = self._process_items_with_quota_split(items, batch)
        status = self._batch_status_from_result(result)
        message = self._batch_message_from_result(result)
        batch.write({
            "state": "done" if status == "done" else ("failed" if status == "failed" else "warning"),
            "finished_at": fields.Datetime.now(),
            "duration": time.monotonic() - started_at,
            "sent_count": result["sent"],
            "imported_count": result["imported"],
            "failed_count": result["failed"],
            "unconfirmed_count": result["unconfirmed"],
            "message": message,
            "verify_response": self._stored_responses(result["verify_responses"]),
            "import_response": self._stored_responses(result["import_responses"]),
        })
        self._refresh_totals()
        return {
            **self._progress_summary(),
            "batch": batch._progress_values()[0],
            "status": status,
            "message": message,
        }

    def _items_from_products(self, products, batch):
        duplicate_product_by_id = products._beyondid_duplicate_product_map()
        products = products.with_context(
            beyondid_duplicate_product_by_id=duplicate_product_by_id,
        )
        items = []
        issue_values = []
        for product in products:
            reason = self._product_skip_reason(product)
            if reason:
                issue_values.append(self._issue_values(
                    product=product,
                    batch=batch,
                    level="skipped",
                    reason=reason,
                    message=self._reason_message(reason),
                ))
                product.sudo().with_context(skip_beyondid_mark_pending=True).write({
                    "beyondid_sync_state": "skipped",
                    "beyondid_sync_reason": reason,
                    "beyondid_needs_sync": False,
                    "beyondid_last_error": False,
                    "beyondid_last_warning": False,
                })
                continue
            row = product._beyondid_payload_values()
            payload_hash = product._beyondid_payload_hash(row)
            if (
                product.beyondid_sync_state == "synced"
                and not product.beyondid_needs_sync
                and product.beyondid_last_payload_hash == payload_hash
            ):
                continue
            items.append({
                "product": product,
                "row": row,
                "hash": payload_hash,
                "key": product._beyondid_product_key(),
            })
        if issue_values:
            self.env["retailit.beyondid.product.initial.load.issue"].create(issue_values)
        return items

    def _csv_from_items(self, items):
        headers = ["itemtype", "productid", "skuid", "code", "price", "name", "members"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow({key: item["row"].get(key, "") for key in headers})
        return stream.getvalue().encode("utf-8")

    def _upload_batch(self, csv_content, operation, batch):
        self.ensure_one()
        self.api_calls += 1
        batch.api_calls += 1
        return self.env["retailit.beyondid.api.client"].upload_products_csv(
            csv_content,
            operation=operation,
            filename="odoo_initial_load_%s_batch_%s_%s.csv" % (self.id, batch.sequence, operation),
        )

    def _clear_batch_retry_issues(self, batch):
        self.env["retailit.beyondid.product.initial.load.issue"].search([
            ("batch_id", "=", batch.id),
            ("level", "in", ["warning", "error"]),
        ]).unlink()

    def _process_items_with_quota_split(self, items, batch):
        result = {
            "sent": 0,
            "imported": 0,
            "failed": 0,
            "unconfirmed": 0,
            "messages": [],
            "verify_responses": [],
            "import_responses": [],
            "quota_exhausted": False,
            "quota_message": False,
            "quota_split_count": 0,
        }
        self._process_item_chunk(items, batch, result)
        return result

    def _process_item_chunk(self, items, batch, result):
        if not items:
            return
        if result["quota_exhausted"]:
            message = result["quota_message"] or self._quota_exceeded_message()
            self._mark_items_failed(items, batch, "quota_exceeded", message, code="import_QUOTAEXCEEDED")
            result["failed"] += len(items)
            return

        csv_content = self._csv_from_items(items)
        verify_response = self._upload_batch(csv_content, "verify", batch)
        result["verify_responses"].append(verify_response)
        if self._response_has_errors(verify_response):
            if self._response_has_quota_exceeded(verify_response):
                self._handle_quota_exceeded_chunk(items, batch, result, verify_response)
                return
            message = self._response_message(verify_response)
            self._mark_items_failed(items, batch, "verify_error", message)
            result["failed"] += len(items)
            result["messages"].append(message)
            return

        batch.state = "importing"
        try:
            import_response = self._upload_batch(csv_content, "import", batch)
        except BeyondIdTransientError as error:
            message = str(error)
            self._mark_items_unconfirmed(items, batch, message)
            result["unconfirmed"] += len(items)
            result["messages"].append(message)
            return
        except UserError as error:
            message = str(error)
            self._mark_items_unconfirmed(items, batch, message)
            result["unconfirmed"] += len(items)
            result["messages"].append(message)
            return

        result["import_responses"].append(import_response)
        if self._response_has_errors(import_response) and self._response_has_quota_exceeded(import_response):
            self._handle_quota_exceeded_chunk(items, batch, result, import_response)
            return

        import_result = self._apply_import_response(items, batch, import_response)
        result["sent"] += import_result["sent"]
        result["imported"] += import_result["imported"]
        result["failed"] += import_result["failed"]
        result["unconfirmed"] += import_result["unconfirmed"]
        result["messages"].append(import_result["message"])

    def _handle_quota_exceeded_chunk(self, items, batch, result, response):
        message = self._quota_exceeded_message(response)
        result["quota_message"] = message
        if len(items) == 1:
            self._mark_items_failed(items, batch, "quota_exceeded", message, code="import_QUOTAEXCEEDED")
            result["failed"] += 1
            result["quota_exhausted"] = True
            result["messages"].append(message)
            return

        result["quota_split_count"] += 1
        midpoint = max(1, len(items) // 2)
        self._process_item_chunk(items[:midpoint], batch, result)
        self._process_item_chunk(items[midpoint:], batch, result)

    def _batch_status_from_result(self, result):
        if result["failed"] and not result["imported"] and not result["unconfirmed"]:
            return "failed"
        if result["failed"] or result["unconfirmed"]:
            return "warning"
        return "done"

    def _batch_message_from_result(self, result):
        if result["failed"] and result["quota_message"]:
            if result["imported"]:
                return _(
                    "Imported %(imported)s products. %(failed)s products could not be imported because the Beyond ID product quota was exceeded. Free capacity or increase the Beyond ID quota, then retry this initial load."
                ) % {
                    "imported": result["imported"],
                    "failed": result["failed"],
                }
            return result["quota_message"]
        unique_messages = []
        for message in result["messages"]:
            if message and message not in unique_messages:
                unique_messages.append(message)
        return "\n".join(unique_messages[:5]) or _("Batch imported successfully.")

    def _stored_responses(self, responses):
        return "\n".join(str(response) for response in responses[-5:])[:10000]

    def _response_error_lines(self, response):
        return [
            line for line in response.get("lines") or []
            if (line.get("level") or "").upper() == "ERROR"
        ]

    def _response_has_errors(self, response):
        totals = response.get("totals") or {}
        try:
            return int(totals.get("ERROR") or 0) > 0
        except (TypeError, ValueError):
            return bool(self._response_error_lines(response))

    def _response_has_quota_exceeded(self, response):
        return any(
            QUOTA_EXCEEDED_CODE in (line.get("code") or "").upper()
            for line in self._response_error_lines(response)
        )

    def _quota_exceeded_message(self, response=False):
        return _(
            "Beyond ID product quota was exceeded. Odoo will retry large batches in smaller parts, but remaining products need more Beyond ID capacity before they can be imported."
        )

    def _response_message(self, response):
        error_lines = self._response_error_lines(response)
        if not error_lines:
            return _("Beyond ID returned an error for this batch.")
        return "\n".join(
            "%s: %s" % (line.get("code") or "ERROR", line.get("data") or line.get("message") or "-")
            for line in error_lines[:5]
        )

    def _apply_import_response(self, items, batch, response):
        if response.get("result") != "OK":
            message = response.get("exceptionmessage") or response.get("message") or str(response)[:500]
            self._mark_items_unconfirmed(items, batch, message)
            return {
                "status": "warning",
                "sent": 0,
                "imported": 0,
                "failed": 0,
                "unconfirmed": len(items),
                "message": message,
            }

        if not self._response_has_errors(response):
            self._mark_items_imported(items, batch)
            return {
                "status": "done",
                "sent": len(items),
                "imported": len(items),
                "failed": 0,
                "unconfirmed": 0,
                "message": _("Batch imported successfully."),
            }

        error_keys = self._error_keys_from_response(response)
        item_by_key = {item["key"]: item for item in items}
        failed_items = [item_by_key[key] for key in error_keys if key in item_by_key]
        unconfirmed_items = [item for item in items if item["key"] not in error_keys]
        message = self._response_message(response)
        if failed_items:
            self._mark_items_failed(failed_items, batch, "remote_duplicate", message)
        if unconfirmed_items:
            self._mark_items_unconfirmed(unconfirmed_items, batch, message)
        return {
            "status": "warning",
            "sent": len(items),
            "imported": 0,
            "failed": len(failed_items),
            "unconfirmed": len(unconfirmed_items),
            "message": _(
                "Beyond ID reported errors. %(failed)s products were identified as failed and %(unconfirmed)s products were left unconfirmed."
            ) % {
                "failed": len(failed_items),
                "unconfirmed": len(unconfirmed_items),
            },
        }

    def _error_keys_from_response(self, response):
        keys = set()
        for line in response.get("lines") or []:
            if (line.get("level") or "").upper() != "ERROR":
                continue
            for source in (line.get("line") or "", line.get("data") or ""):
                for product_id, sku_id in PRODUCT_LINE_RE.findall(source):
                    keys.add("%s|%s" % (product_id, sku_id))
            data = line.get("data") or ""
            for product_id, sku_id in SQL_PRODUCT_RE.findall(data):
                if product_id.isdigit() and sku_id.isdigit():
                    keys.add("%s|%s" % (product_id, sku_id))
        return keys

    def _mark_items_imported(self, items, batch):
        now = fields.Datetime.now()
        for item in items:
            product = item["product"]
            product.sudo().with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "synced",
                "beyondid_sync_reason": False,
                "beyondid_needs_sync": False,
                "beyondid_last_sync_date": now,
                "beyondid_last_sync_attempt_date": now,
                "beyondid_last_payload_hash": item.get("hash"),
                "beyondid_last_code": item["row"].get("code"),
                "beyondid_external_productid": item["row"].get("productid"),
                "beyondid_external_skuid": item["row"].get("skuid"),
                "beyondid_last_warning": False,
                "beyondid_last_error": False,
                "beyondid_last_operation": "import",
            })

    def _mark_items_failed(self, items, batch, reason, message, code=False):
        issue_values = []
        for item in items:
            product = item["product"]
            issue_values.append(self._issue_values(
                product=product,
                batch=batch,
                level="error",
                reason=reason,
                code=code or reason,
                message=message,
            ))
            product.sudo().with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "failed",
                "beyondid_sync_reason": "api_error",
                "beyondid_needs_sync": True,
                "beyondid_last_error": message,
                "beyondid_last_warning": False,
                "beyondid_last_operation": "import",
            })
        if issue_values:
            self.env["retailit.beyondid.product.initial.load.issue"].create(issue_values)

    def _mark_items_unconfirmed(self, items, batch, message):
        issue_values = []
        for item in items:
            product = item["product"]
            issue_values.append(self._issue_values(
                product=product,
                batch=batch,
                level="warning",
                reason="unconfirmed",
                code="unconfirmed",
                message=message,
            ))
            product.sudo().with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "unconfirmed",
                "beyondid_sync_reason": "api_error",
                "beyondid_needs_sync": True,
                "beyondid_last_error": message,
                "beyondid_last_warning": self._reason_message("unconfirmed"),
                "beyondid_last_operation": "import",
            })
        if issue_values:
            self.env["retailit.beyondid.product.initial.load.issue"].create(issue_values)

    def _refresh_totals(self):
        self.ensure_one()
        batches = self.batch_ids
        self.write({
            "total_sent": sum(batches.mapped("sent_count")),
            "total_imported": sum(batches.mapped("imported_count")),
            "total_failed": sum(batches.mapped("failed_count")),
            "total_unconfirmed": sum(batches.mapped("unconfirmed_count")),
            "api_calls": sum(batches.mapped("api_calls")),
            "duration": self._progress_duration(),
        })

    @api.model
    def action_initial_load_finalize(self, load_id):
        load = self.browse(load_id).exists()
        if not load:
            raise UserError(_("The initial product load no longer exists."))
        state = "done"
        if load.total_failed or load.total_unconfirmed or load.issue_ids.filtered(lambda issue: issue.level in ("warning", "error")):
            state = "warning"
        load.write({
            "state": state,
            "finished_at": fields.Datetime.now(),
            "duration": load._progress_duration(),
        })
        return load._progress_summary()


class BeyondIdProductInitialLoadBatch(models.Model):
    _name = "retailit.beyondid.product.initial.load.batch"
    _description = "Beyond ID Initial Product Load Batch"
    _order = "load_id desc, sequence asc"

    load_id = fields.Many2one("retailit.beyondid.product.initial.load", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(required=True, index=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("verifying", "Verifying"),
            ("importing", "Importing"),
            ("done", "Done"),
            ("warning", "Warning"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    product_ids = fields.Many2many(
        "product.product",
        "beyondid_initial_load_batch_product_rel",
        "batch_id",
        "product_id",
        string="Products",
        readonly=True,
    )
    product_count = fields.Integer(readonly=True)
    sent_count = fields.Integer(readonly=True)
    imported_count = fields.Integer(readonly=True)
    failed_count = fields.Integer(readonly=True)
    unconfirmed_count = fields.Integer(readonly=True)
    api_calls = fields.Integer(readonly=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    duration = fields.Float(string="Duration (s)", readonly=True)
    message = fields.Text(readonly=True)
    verify_response = fields.Text(readonly=True)
    import_response = fields.Text(readonly=True)

    def _progress_values(self):
        return [
            {
                "id": batch.id,
                "sequence": batch.sequence,
                "state": batch.state,
                "product_count": batch.product_count,
                "sent_count": batch.sent_count,
                "imported_count": batch.imported_count,
                "failed_count": batch.failed_count,
                "unconfirmed_count": batch.unconfirmed_count,
                "api_calls": batch.api_calls,
                "message": batch.message or "",
            }
            for batch in self
        ]


class BeyondIdProductInitialLoadIssue(models.Model):
    _name = "retailit.beyondid.product.initial.load.issue"
    _description = "Beyond ID Initial Product Load Issue"
    _order = "create_date desc, id desc"

    load_id = fields.Many2one("retailit.beyondid.product.initial.load", required=True, ondelete="cascade", index=True)
    batch_id = fields.Many2one("retailit.beyondid.product.initial.load.batch", ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", ondelete="set null", index=True)
    level = fields.Selection(
        [
            ("skipped", "Skipped"),
            ("warning", "Warning"),
            ("error", "Error"),
        ],
        required=True,
        default="error",
        index=True,
    )
    reason = fields.Selection(
        [
            ("missing_barcode", "Missing Barcode"),
            ("duplicate_barcode", "Duplicate Barcode"),
            ("missing_name", "Missing Name"),
            ("verify_error", "Verification Error"),
            ("api_error", "API Error"),
            ("quota_exceeded", "Quota Exceeded"),
            ("remote_duplicate", "Remote Duplicate"),
            ("unconfirmed", "Unconfirmed"),
        ],
        index=True,
    )
    code = fields.Char(readonly=True)
    message = fields.Text(required=True)
    raw_line = fields.Text(readonly=True)

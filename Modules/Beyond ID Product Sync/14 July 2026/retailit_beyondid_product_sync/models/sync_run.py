import csv
import io
import re
import time

from odoo.addons.retailit_beyondid_manager.models.retailit_beyondid_api_client import BeyondIdTransientError
from odoo import _, api, fields, models
from odoo.exceptions import UserError


PRODUCT_LINE_RE = re.compile(r"productid:([^\s]+)\s+skuid:([^\s]+)", re.IGNORECASE)
DUPLICATE_PRODUCT_KEY_RE = re.compile(
    r"Key \(app, productid, skuid\)=\([^,]+,\s*([^,\s\)]+),\s*([^,\s\)]+)\)",
    re.IGNORECASE,
)
TRANSIENT_RETRY_COUNT = 1
TRANSIENT_SPLIT_SIZE = 5
MANUAL_PROGRESS_SYNC_LIMIT = 500


class BeyondIdProductSyncRun(models.Model):
    _name = "retailit.beyondid.product.sync.run"
    _description = "Beyond ID Product Sync Execution"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default=lambda self: self._default_name())
    execution_type = fields.Selection(
        [
            ("manual", "Manual"),
            ("cron", "Cron"),
            ("reset", "Reset"),
        ],
        required=True,
        default="manual",
        index=True,
    )
    operation = fields.Selection(
        [
            ("mixed", "Mixed"),
            ("import", "Import"),
            ("delete", "Delete"),
            ("reset", "Reset"),
        ],
        required=True,
        default="mixed",
        index=True,
    )
    state = fields.Selection(
        [
            ("running", "Running"),
            ("done", "Done"),
            ("warning", "Warning"),
            ("failed", "Failed"),
        ],
        required=True,
        default="running",
        index=True,
    )
    started_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    finished_at = fields.Datetime(index=True)
    duration = fields.Float(string="Duration (s)", readonly=True)
    odoo_prepare_duration = fields.Float(string="Odoo Prepare (s)", readonly=True)
    auth_duration = fields.Float(string="Authentication (s)", readonly=True)
    api_duration = fields.Float(string="Beyond ID API (s)", readonly=True)
    odoo_apply_duration = fields.Float(string="Odoo Apply (s)", readonly=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    total_evaluated = fields.Integer(readonly=True)
    total_sent = fields.Integer(readonly=True)
    total_synced = fields.Integer(readonly=True)
    total_warnings = fields.Integer(readonly=True)
    total_failed = fields.Integer(readonly=True)
    total_skipped = fields.Integer(readonly=True)
    total_no_changes = fields.Integer(readonly=True)
    total_reset = fields.Integer(readonly=True)
    api_calls = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    issue_ids = fields.One2many("retailit.beyondid.product.sync.issue", "run_id", readonly=True)
    issue_count = fields.Integer(compute="_compute_issue_count")

    @api.model
    def _default_name(self):
        return _("Beyond ID Sync %s") % fields.Datetime.now()

    @api.depends("issue_ids")
    def _compute_issue_count(self):
        for run in self:
            run.issue_count = len(run.issue_ids)

    @api.model
    def create_and_execute(self, products, execution_type="manual", raise_on_error=True):
        run = self.create({
            "name": _("Beyond ID %s") % dict(self._fields["execution_type"].selection).get(execution_type, execution_type),
            "execution_type": execution_type,
            "operation": "mixed",
        })
        start = time.monotonic()
        try:
            run._execute(products)
        except Exception as error:
            run.write({
                "state": "failed",
                "finished_at": fields.Datetime.now(),
                "duration": time.monotonic() - start,
                "error_message": str(error),
            })
            if raise_on_error:
                raise
            return run
        run.write({
            "finished_at": fields.Datetime.now(),
            "duration": time.monotonic() - start,
        })
        run._finalize_state()
        return run

    def _add_elapsed(self, field_name, started_at):
        self.ensure_one()
        self.write({
            field_name: (self[field_name] or 0.0) + (time.monotonic() - started_at),
        })

    def _execute(self, products):
        self.ensure_one()
        client = self.env["retailit.beyondid.api.client"]
        prepare_start = time.monotonic()
        config = client._get_config()
        if not config.get("enabled"):
            raise UserError(_("Enable the Beyond ID integration before synchronizing products."))
        client._validate_required_config(config=config, require_workspace=True)

        products = self._lock_products(products.with_context(active_test=False))
        self.total_evaluated = len(products)
        if not products:
            self._add_elapsed("odoo_prepare_duration", prepare_start)
            return True

        import_items, delete_items = self._prepare_sync_items(products)

        self._add_elapsed("odoo_prepare_duration", prepare_start)

        authorized_context = None
        if delete_items or import_items:
            auth_start = time.monotonic()
            auth_error = False
            try:
                auth_config, auth_headers = client._get_authorized_context(config=config)
                authorized_context = {
                    "config": auth_config,
                    "headers": auth_headers,
                }
            except UserError as error:
                auth_error = str(error)
            finally:
                self._add_elapsed("auth_duration", auth_start)
            if auth_error:
                apply_start = time.monotonic()
                self._mark_batch_failed(delete_items, "delete", auth_error)
                self._mark_batch_failed(import_items, "import", auth_error)
                self._add_elapsed("odoo_apply_duration", apply_start)
                return False

        self._process_operation_batches(delete_items, "delete", authorized_context=authorized_context)
        self._process_operation_batches(import_items, "import", authorized_context=authorized_context)
        return True

    def _prepare_sync_items(self, products):
        duplicate_product_by_id = products._beyondid_duplicate_product_map()
        products = products.with_context(
            beyondid_duplicate_product_by_id=duplicate_product_by_id,
        )
        import_items = []
        delete_items = []
        for product in products:
            item = product._beyondid_prepare_sync_item()
            action = item["action"]
            if action == "ignore":
                self._apply_local_ignore(product, item["reason"])
            elif action == "skip":
                self._apply_local_skip(product, item["reason"])
            elif action == "no_change":
                self.total_no_changes += 1
                product.with_context(skip_beyondid_mark_pending=True).write({
                    "beyondid_needs_sync": False,
                    "beyondid_sync_reason": False,
                })
            elif action == "delete":
                item["product"] = product
                delete_items.append(item)
            else:
                item["product"] = product
                import_items.append(item)
        return import_items, delete_items

    def _lock_products(self, products):
        if not products:
            return products
        self.env.cr.execute(
            "SELECT id FROM product_product WHERE id = ANY(%s) FOR UPDATE SKIP LOCKED",
            [products.ids],
        )
        locked_ids = [row[0] for row in self.env.cr.fetchall()]
        return products.browse(locked_ids)

    def _batch_size(self):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "retailit_beyondid_product_sync.batch_size",
            "25",
        )
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 25
        return max(1, min(value, 1000))

    def _process_operation_batches(self, items, operation, authorized_context=None):
        if not items:
            return True
        batch_size = self._batch_size()
        for grouped_items, upload_options in self._group_items_by_upload_options(items, operation):
            for index in range(0, len(grouped_items), batch_size):
                batch = grouped_items[index:index + batch_size]
                self._process_api_batch(
                    batch,
                    operation,
                    authorized_context=authorized_context,
                    upload_options=upload_options,
                )
        return True

    def _group_items_by_upload_options(self, items, operation):
        if operation != "import":
            return [(items, {})]
        new_items = []
        update_items = []
        for item in items:
            if item.get("update_only"):
                update_items.append(item)
            else:
                new_items.append(item)
        grouped = []
        if new_items:
            grouped.append((new_items, {}))
        if update_items:
            grouped.append((update_items, {"updateonly": "true"}))
        return grouped

    def _process_api_batch(self, items, operation, authorized_context=None, upload_options=None):
        prepare_start = time.monotonic()
        products = self.env["product.product"].concat(*(item["product"] for item in items))
        products.sudo().with_context(skip_beyondid_mark_pending=True).write({
            "beyondid_sync_state": "processing",
            "beyondid_last_sync_attempt_date": fields.Datetime.now(),
            "beyondid_last_run_id": self.id,
        })

        self.total_sent += len(items)
        self._add_elapsed("odoo_prepare_duration", prepare_start)

        return self._send_api_batch_items(
            items,
            operation,
            authorized_context=authorized_context,
            allow_split=True,
            upload_options=upload_options,
        )

    def _send_api_batch_items(
        self,
        items,
        operation,
        authorized_context=None,
        allow_split=False,
        upload_options=None,
        allow_update_missing_fallback=True,
        retry_update_on_duplicate=False,
    ):
        response = False
        try:
            response = self._upload_api_batch_with_retries(
                items,
                operation,
                authorized_context=authorized_context,
                upload_options=upload_options,
            )
        except BeyondIdTransientError as error:
            if allow_split and len(items) > TRANSIENT_SPLIT_SIZE:
                success = True
                for sub_items in self._split_items(items, TRANSIENT_SPLIT_SIZE):
                    success = self._send_api_batch_items(
                        sub_items,
                        operation,
                        authorized_context=authorized_context,
                        allow_split=False,
                        upload_options=upload_options,
                        allow_update_missing_fallback=allow_update_missing_fallback,
                        retry_update_on_duplicate=retry_update_on_duplicate,
                    ) and success
                return success
            apply_start = time.monotonic()
            self._mark_batch_failed(items, operation, str(error), retryable=True)
            self._add_elapsed("odoo_apply_duration", apply_start)
            return False
        except UserError as error:
            apply_start = time.monotonic()
            self._mark_batch_failed(items, operation, str(error), retryable=True)
            self._add_elapsed("odoo_apply_duration", apply_start)
            return False

        if operation == "import" and self._is_update_only_upload(upload_options):
            fallback_items, response = self._extract_update_not_found_fallback_items(items, response)
            if fallback_items:
                fallback_keys = {item.get("key") for item in fallback_items}
                remaining_items = [
                    item for item in items
                    if item.get("key") not in fallback_keys
                ]
                if remaining_items:
                    apply_start = time.monotonic()
                    self._apply_api_response(remaining_items, operation, response)
                    self._add_elapsed("odoo_apply_duration", apply_start)
                if not allow_update_missing_fallback:
                    apply_start = time.monotonic()
                    self._mark_update_missing_pending(fallback_items, operation)
                    self._add_elapsed("odoo_apply_duration", apply_start)
                    return False
                return self._send_api_batch_items(
                    fallback_items,
                    operation,
                    authorized_context=authorized_context,
                    allow_split=allow_split,
                    upload_options={},
                    retry_update_on_duplicate=True,
                )

        if operation == "import" and retry_update_on_duplicate:
            retry_items, response = self._extract_duplicate_key_retry_items(items, response)
            if retry_items:
                retry_keys = {item.get("key") for item in retry_items}
                remaining_items = [
                    item for item in items
                    if item.get("key") not in retry_keys
                ]
                if remaining_items:
                    apply_start = time.monotonic()
                    self._apply_api_response(remaining_items, operation, response)
                    self._add_elapsed("odoo_apply_duration", apply_start)
                return self._send_api_batch_items(
                    retry_items,
                    operation,
                    authorized_context=authorized_context,
                    allow_split=False,
                    upload_options={"updateonly": "true"},
                    allow_update_missing_fallback=False,
                )

        apply_start = time.monotonic()
        self._apply_api_response(items, operation, response)
        self._add_elapsed("odoo_apply_duration", apply_start)
        return True

    def _is_update_only_upload(self, upload_options):
        return str((upload_options or {}).get("updateonly", "")).lower() == "true"

    def _extract_update_not_found_fallback_items(self, items, response):
        fallback_keys = set()
        filtered_lines = []
        for line in response.get("lines") or []:
            key = self._key_from_line(line)
            if key and line.get("code") == "import_updateItemNotFound":
                fallback_keys.add(key)
                continue
            filtered_lines.append(line)
        if not fallback_keys:
            return [], response
        fallback_items = [
            item for item in items
            if item.get("key") in fallback_keys
        ]
        filtered_response = dict(response)
        filtered_response["lines"] = filtered_lines
        return fallback_items, filtered_response

    def _extract_duplicate_key_retry_items(self, items, response):
        retry_keys = set()
        duplicate_lines = []
        filtered_lines = []
        for line in response.get("lines") or []:
            key = self._duplicate_key_from_line(line)
            if key:
                retry_keys.add(key)
                duplicate_lines.append(line)
                continue
            filtered_lines.append(line)
        if duplicate_lines and not retry_keys and len(items) == 1:
            retry_keys.add(items[0].get("key"))
        if not retry_keys:
            return [], response
        retry_items = [
            item for item in items
            if item.get("key") in retry_keys
        ]
        filtered_response = dict(response)
        filtered_response["lines"] = filtered_lines
        return retry_items, filtered_response

    def _duplicate_key_from_line(self, line):
        text = line.get("data") or line.get("message") or line.get("raw_line") or ""
        if "appitem_app_productid_skuid" not in text:
            return False
        if "duplicate key value" not in text:
            return False
        match = DUPLICATE_PRODUCT_KEY_RE.search(text)
        if not match:
            return False
        product_id = match.group(1).strip("'\"")
        sku_id = match.group(2).strip("'\"")
        return "%s|%s" % (product_id, sku_id)

    def _mark_update_missing_pending(self, items, operation):
        self.total_warnings += len(items)
        self._mark_batch_pending(
            items,
            operation,
            _(
                "Beyond ID temporarily reported this existing product as missing. "
                "Odoo will retry it as an update in the next sync."
            ),
        )

    def _upload_api_batch_with_retries(self, items, operation, authorized_context=None, upload_options=None):
        attempts = TRANSIENT_RETRY_COUNT + 1
        last_error = False
        for _attempt in range(attempts):
            prepare_start = time.monotonic()
            csv_content = self._csv_from_items(items)
            self._add_elapsed("odoo_prepare_duration", prepare_start)

            api_start = time.monotonic()
            self.api_calls += 1
            try:
                response = self.env["retailit.beyondid.api.client"].upload_products_csv(
                    csv_content,
                    operation=operation,
                    filename="odoo_products_%s_%s.csv" % (operation, self.id),
                    authorized_context=authorized_context,
                    upload_options=upload_options,
                )
            except BeyondIdTransientError as error:
                last_error = error
                self._add_elapsed("api_duration", api_start)
                continue
            except UserError:
                self._add_elapsed("api_duration", api_start)
                raise
            self._add_elapsed("api_duration", api_start)
            return response
        raise last_error

    def _split_items(self, items, size):
        for index in range(0, len(items), size):
            yield items[index:index + size]

    def _split_product_ids(self, product_ids, size):
        for index in range(0, len(product_ids), size):
            yield product_ids[index:index + size]

    def _csv_from_items(self, items):
        headers = ["itemtype", "productid", "skuid", "code", "price", "name", "members"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            row = {key: item["row"].get(key, "") for key in headers}
            writer.writerow(row)
        return stream.getvalue().encode("utf-8")

    def _apply_local_skip(self, product, reason):
        self.total_skipped += 1
        self._create_issue(
            product=product,
            level="skipped",
            operation="local",
            reason=reason,
            message=self._reason_message(reason),
        )
        product.sudo().with_context(skip_beyondid_mark_pending=True).write({
            "beyondid_sync_state": "skipped",
            "beyondid_sync_reason": reason,
            "beyondid_needs_sync": False,
            "beyondid_last_error": False,
            "beyondid_last_warning": False,
            "beyondid_last_run_id": self.id,
        })

    def _apply_local_ignore(self, product, reason):
        self.total_skipped += 1
        product.sudo().with_context(skip_beyondid_mark_pending=True).write({
            "beyondid_sync_state": "skipped",
            "beyondid_sync_reason": reason,
            "beyondid_needs_sync": False,
            "beyondid_last_error": False,
            "beyondid_last_warning": False,
            "beyondid_last_run_id": self.id,
        })

    def _reason_message(self, reason):
        messages = {
            "missing_barcode": _("The product was not sent because it has no barcode."),
            "duplicate_barcode": _("The product was not sent because another active variant uses the same barcode."),
            "missing_name": _("The product was not sent because it has no name."),
            "inactive_product": _("The product variant is archived in Odoo."),
            "inactive_template": _("The product template is archived in Odoo."),
            "api_error": _("Beyond ID rejected the product or did not respond correctly."),
            "validation_error": _("The product did not pass validation."),
        }
        return messages.get(reason, reason or _("The product was skipped."))

    def _mark_batch_failed(self, items, operation, message, retryable=True):
        self.total_failed += len(items)
        reason = "api_error" if retryable else "validation_error"
        for item in items:
            product = item["product"]
            self._create_issue(
                product=product,
                level="error",
                operation=operation,
                reason=reason,
                code=reason,
                message=message,
                product_key=item.get("key"),
            )
            product.sudo().with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "failed",
                "beyondid_sync_reason": reason,
                "beyondid_needs_sync": bool(retryable),
                "beyondid_last_error": message,
                "beyondid_last_operation": operation,
                "beyondid_last_run_id": self.id,
            })

    def _mark_batch_pending(self, items, operation, message):
        for item in items:
            item["product"].sudo().with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "pending",
                "beyondid_sync_reason": "api_error",
                "beyondid_needs_sync": True,
                "beyondid_last_error": message,
                "beyondid_last_operation": operation,
                "beyondid_last_run_id": self.id,
            })

    def _apply_api_response(self, items, operation, response):
        result = response.get("result")
        if result != "OK":
            message = response.get("exceptionmessage") or response.get("message") or str(response)[:500]
            self._mark_batch_failed(items, operation, message)
            return False

        messages = self._messages_by_key(response)
        global_messages = messages.pop("__global__", [])
        global_errors = [line for line in global_messages if self._line_level(line) == "error"]
        global_warnings = [line for line in global_messages if self._line_level(line) == "warning"]
        for line in global_messages:
            self._create_issue_from_line(line, operation=operation)
        if global_warnings:
            self.total_warnings += len(global_warnings)
        if global_errors:
            self._mark_batch_failed(
                items,
                operation,
                self._line_message(global_errors),
            )
            return False

        item_by_key = {item["key"]: item for item in items}
        for key, line_messages in messages.items():
            if key not in item_by_key:
                for line in line_messages:
                    level = self._line_level(line)
                    if level == "error":
                        self.total_failed += 1
                    elif level == "warning":
                        self.total_warnings += 1
                    self._create_issue_from_line(line, operation=operation)

        for item in items:
            product = item["product"]
            line_messages = messages.get(item["key"], [])
            errors = [line for line in line_messages if self._line_level(line) == "error"]
            warnings = [line for line in line_messages if self._line_level(line) == "warning"]
            if errors:
                message = self._line_message(errors)
                self.total_failed += 1
                for line in errors:
                    self._create_issue_from_line(line, product=product, operation=operation, reason="validation_error")
                product.sudo().with_context(skip_beyondid_mark_pending=True).write({
                    "beyondid_sync_state": "failed",
                    "beyondid_sync_reason": "validation_error",
                    "beyondid_needs_sync": False,
                    "beyondid_last_error": message,
                    "beyondid_last_operation": operation,
                    "beyondid_last_run_id": self.id,
                })
                continue
            if warnings:
                message = self._line_message(warnings)
                self.total_warnings += 1
                for line in warnings:
                    self._create_issue_from_line(line, product=product, operation=operation, reason="validation_error")
                self._mark_product_success(product, item, operation, state="warning", warning=message)
                continue
            self._mark_product_success(product, item, operation)
        return True

    def _messages_by_key(self, response):
        messages = {}
        for line in response.get("lines") or []:
            key = self._key_from_line(line)
            if key:
                messages.setdefault(key, []).append(line)
            elif self._line_level(line) in ("warning", "error"):
                messages.setdefault("__global__", []).append(line)
        return messages

    def _key_from_line(self, line):
        text = line.get("line") or line.get("data") or ""
        match = PRODUCT_LINE_RE.search(text)
        if not match:
            return False
        return "%s|%s" % (match.group(1), match.group(2))

    def _line_level(self, line):
        level = (line.get("level") or "").lower()
        if level == "warning":
            return "warning"
        if level == "error":
            return "error"
        return "info"

    def _line_message(self, lines):
        return "\n".join(
            "%s: %s" % (line.get("code") or line.get("level"), line.get("data") or line.get("message") or "-")
            for line in lines
        )

    def _create_issue_from_line(self, line, product=False, operation="import", reason=False):
        level = self._line_level(line)
        self._create_issue(
            product=product,
            level=level,
            operation=operation,
            reason=reason,
            code=line.get("code"),
            message=line.get("data") or line.get("message") or line.get("code") or str(line),
            product_key=self._key_from_line(line),
            raw_line=str(line),
        )

    def _create_issue(self, product=False, level="error", operation="local", reason=False, code=False, message=False, product_key=False, raw_line=False):
        self.env["retailit.beyondid.product.sync.issue"].create({
            "run_id": self.id,
            "product_id": product.id if product else False,
            "level": level,
            "operation": operation,
            "reason": reason,
            "code": code,
            "message": message or "-",
            "product_key": product_key,
            "raw_line": raw_line,
        })

    def _mark_product_success(self, product, item, operation, state="synced", warning=False):
        values = {
            "beyondid_sync_state": state,
            "beyondid_sync_reason": False,
            "beyondid_needs_sync": False,
            "beyondid_last_sync_date": fields.Datetime.now(),
            "beyondid_last_error": False,
            "beyondid_last_warning": warning or False,
            "beyondid_last_operation": operation,
            "beyondid_last_run_id": self.id,
        }
        if operation == "delete":
            values.update({
                "beyondid_sync_state": "skipped",
                "beyondid_sync_reason": item.get("reason"),
                "beyondid_last_payload_hash": False,
                "beyondid_last_code": False,
            })
            self.total_skipped += 1
        else:
            values.update({
                "beyondid_last_payload_hash": item.get("hash"),
                "beyondid_last_code": item["row"].get("code"),
                "beyondid_external_productid": item["row"].get("productid"),
                "beyondid_external_skuid": item["row"].get("skuid"),
            })
            self.total_synced += 1
        product.sudo().with_context(skip_beyondid_mark_pending=True).write(values)

    def _finalize_state(self):
        self.ensure_one()
        state = "done"
        if self.error_message or self.total_failed:
            state = "failed"
        elif self.total_warnings:
            state = "warning"
        self.state = state

    def _progress_duration(self):
        self.ensure_one()
        if not self.started_at:
            return 0.0
        return (fields.Datetime.now() - self.started_at).total_seconds()

    def _progress_summary(self):
        self.ensure_one()
        return {
            "run_id": self.id,
            "state": self.state,
            "total_evaluated": self.total_evaluated or 0,
            "total_sent": self.total_sent or 0,
            "total_synced": self.total_synced or 0,
            "total_warnings": self.total_warnings or 0,
            "total_failed": self.total_failed or 0,
            "total_skipped": self.total_skipped or 0,
            "total_no_changes": self.total_no_changes or 0,
            "total_reset": self.total_reset or 0,
            "api_calls": self.api_calls or 0,
            "duration": self.duration or self._progress_duration(),
        }

    def _progress_batch_items(self, product_ids, operation):
        products = self.env["product.product"].with_context(active_test=False).browse(product_ids).exists()
        duplicate_product_by_id = products._beyondid_duplicate_product_map()
        products = products.with_context(
            beyondid_duplicate_product_by_id=duplicate_product_by_id,
        )
        items = []
        changed_items = []
        for product in products:
            item = product._beyondid_prepare_sync_item()
            action = item["action"]
            if action == operation:
                item["product"] = product
                items.append(item)
            elif action == "ignore":
                self._apply_local_ignore(product, item["reason"])
            elif action == "skip":
                self._apply_local_skip(product, item["reason"])
            elif action == "no_change":
                self.total_no_changes += 1
                product.with_context(skip_beyondid_mark_pending=True).write({
                    "beyondid_needs_sync": False,
                    "beyondid_sync_reason": False,
                })
            else:
                item["product"] = product
                changed_items.append(item)
        if changed_items:
            self._mark_batch_failed(
                changed_items,
                operation,
                _("The product changed while the sync was running. Please run Sync Selected again for this product."),
                retryable=False,
            )
        return items

    @api.model
    def action_progress_prepare(self, product_ids):
        product_ids = [int(product_id) for product_id in product_ids or [] if product_id]
        if len(product_ids) > MANUAL_PROGRESS_SYNC_LIMIT:
            raise UserError(_(
                "You selected %(count)s products. Manual sync supports up to %(limit)s products at a time. "
                "Please reduce the selection. Large updates are handled by the automatic sync."
            ) % {
                "count": len(product_ids),
                "limit": MANUAL_PROGRESS_SYNC_LIMIT,
            })

        run = self.create({
            "name": _("Beyond ID Manual"),
            "execution_type": "manual",
            "operation": "mixed",
        })
        prepare_start = time.monotonic()
        try:
            client = self.env["retailit.beyondid.api.client"]
            config = client._get_config()
            if not config.get("enabled"):
                raise UserError(_("Enable the Beyond ID integration before synchronizing products."))
            client._validate_required_config(config=config, require_workspace=True)

            products = self.env["product.product"].with_context(active_test=False).browse(product_ids).exists()
            products = run._lock_products(products)
            run.total_evaluated = len(products)
            import_items, delete_items = run._prepare_sync_items(products)
            run._add_elapsed("odoo_prepare_duration", prepare_start)

            batches = []
            batch_size = run._batch_size()
            for operation, items in (("delete", delete_items), ("import", import_items)):
                for grouped_items, upload_options in run._group_items_by_upload_options(items, operation):
                    ids = [item["product"].id for item in grouped_items]
                    for batch_product_ids in run._split_product_ids(ids, batch_size):
                        batches.append({
                            "operation": operation,
                            "product_ids": batch_product_ids,
                            "count": len(batch_product_ids),
                            "upload_options": upload_options,
                        })

            operations = {batch["operation"] for batch in batches}
            if len(operations) == 1:
                run.operation = next(iter(operations))
            elif operations:
                run.operation = "mixed"

            return {
                **run._progress_summary(),
                "batch_size": batch_size,
                "batches": batches,
                "total_batches": len(batches),
            }
        except Exception as error:
            run.write({
                "state": "failed",
                "finished_at": fields.Datetime.now(),
                "duration": run._progress_duration(),
                "error_message": str(error),
            })
            raise

    @api.model
    def action_progress_authenticate(self, run_id):
        run = self.browse(run_id).exists()
        if not run:
            raise UserError(_("The Beyond ID sync run no longer exists."))
        auth_start = time.monotonic()
        try:
            client = self.env["retailit.beyondid.api.client"]
            config = client._get_config()
            client._get_authorized_context(config=config)
        except UserError as error:
            run.write({
                "state": "failed",
                "finished_at": fields.Datetime.now(),
                "duration": run._progress_duration(),
                "error_message": str(error),
            })
            raise
        finally:
            run._add_elapsed("auth_duration", auth_start)
        return run._progress_summary()

    @api.model
    def action_progress_process_batch(self, run_id, product_ids, operation, count_sent=True, mark_final_error=False, upload_options=None):
        run = self.browse(run_id).exists()
        if not run:
            raise UserError(_("The Beyond ID sync run no longer exists."))
        if operation not in ("import", "delete"):
            raise UserError(_("Unsupported Beyond ID product operation: %s") % operation)

        prepare_start = time.monotonic()
        items = run._progress_batch_items(product_ids, operation)
        if upload_options is None:
            grouped_items = run._group_items_by_upload_options(items, operation)
            if len(grouped_items) == 1:
                upload_options = grouped_items[0][1]
            elif grouped_items:
                raise UserError(_("The progress batch mixes new products and existing updates. Please restart the sync."))
        products = self.env["product.product"].concat(*(item["product"] for item in items)) if items else self.env["product.product"]
        if products:
            products.sudo().with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "processing",
                "beyondid_last_sync_attempt_date": fields.Datetime.now(),
                "beyondid_last_run_id": run.id,
            })
        if count_sent:
            run.total_sent += len(items)
        csv_content = run._csv_from_items(items) if items else b""
        run._add_elapsed("odoo_prepare_duration", prepare_start)
        if not items:
            return {
                **run._progress_summary(),
                "status": "empty",
                "message": _("No products needed to be sent in this batch."),
                "api_calls_delta": 0,
            }

        api_calls_before = run.api_calls or 0
        api_start = time.monotonic()
        run.api_calls += 1
        try:
            response = self.env["retailit.beyondid.api.client"].upload_products_csv(
                csv_content,
                operation=operation,
                filename="odoo_products_%s_%s.csv" % (operation, run.id),
                upload_options=upload_options,
            )
        except BeyondIdTransientError as error:
            run._add_elapsed("api_duration", api_start)
            apply_start = time.monotonic()
            if mark_final_error:
                run._mark_batch_failed(items, operation, str(error), retryable=True)
                run._add_elapsed("odoo_apply_duration", apply_start)
                return {
                    **run._progress_summary(),
                    "status": "failed",
                    "message": str(error),
                    "api_calls_delta": (run.api_calls or 0) - api_calls_before,
                }
            run._mark_batch_pending(items, operation, str(error))
            run._add_elapsed("odoo_apply_duration", apply_start)
            return {
                **run._progress_summary(),
                "status": "transient_error",
                "message": str(error),
                "api_calls_delta": (run.api_calls or 0) - api_calls_before,
            }
        except UserError as error:
            run._add_elapsed("api_duration", api_start)
            apply_start = time.monotonic()
            run._mark_batch_failed(items, operation, str(error), retryable=True)
            run._add_elapsed("odoo_apply_duration", apply_start)
            return {
                **run._progress_summary(),
                "status": "failed",
                "message": str(error),
                "api_calls_delta": (run.api_calls or 0) - api_calls_before,
            }
        run._add_elapsed("api_duration", api_start)

        if operation == "import" and run._is_update_only_upload(upload_options):
            fallback_items, response = run._extract_update_not_found_fallback_items(items, response)
            if fallback_items:
                fallback_keys = {item.get("key") for item in fallback_items}
                remaining_items = [
                    item for item in items
                    if item.get("key") not in fallback_keys
                ]
                if remaining_items:
                    apply_start = time.monotonic()
                    run._apply_api_response(remaining_items, operation, response)
                    run._add_elapsed("odoo_apply_duration", apply_start)
                run._send_api_batch_items(
                    fallback_items,
                    operation,
                    upload_options={},
                    retry_update_on_duplicate=True,
                )
                return {
                    **run._progress_summary(),
                    "status": "failed" if run.total_failed else ("warning" if run.total_warnings else "done"),
                    "message": _("Batch completed."),
                    "api_calls_delta": (run.api_calls or 0) - api_calls_before,
                }

        apply_start = time.monotonic()
        run._apply_api_response(items, operation, response)
        run._add_elapsed("odoo_apply_duration", apply_start)
        return {
            **run._progress_summary(),
            "status": "done",
            "message": _("Batch completed."),
            "api_calls_delta": (run.api_calls or 0) - api_calls_before,
        }

    @api.model
    def action_progress_mark_batch_failed(self, run_id, product_ids, operation, message):
        run = self.browse(run_id).exists()
        if not run:
            raise UserError(_("The Beyond ID sync run no longer exists."))
        if operation not in ("import", "delete"):
            raise UserError(_("Unsupported Beyond ID product operation: %s") % operation)
        items = run._progress_batch_items(product_ids, operation)
        if items:
            apply_start = time.monotonic()
            run._mark_batch_failed(items, operation, message, retryable=True)
            run._add_elapsed("odoo_apply_duration", apply_start)
        return {
            **run._progress_summary(),
            "status": "failed",
            "message": message,
            "api_calls_delta": 0,
        }

    @api.model
    def action_progress_finalize(self, run_id):
        run = self.browse(run_id).exists()
        if not run:
            raise UserError(_("The Beyond ID sync run no longer exists."))
        run.write({
            "finished_at": fields.Datetime.now(),
            "duration": run._progress_duration(),
        })
        run._finalize_state()
        return run._progress_summary()

    def action_view_issues(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Beyond ID Sync Issues"),
            "res_model": "retailit.beyondid.product.sync.issue",
            "view_mode": "list,form",
            "domain": [("run_id", "=", self.id)],
            "target": "current",
        }

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from odoo import _, api, fields, models
from odoo.exceptions import UserError

MANUAL_SYNC_LIMIT = 500


SYNC_STATES = [
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("synced", "Synced"),
    ("unconfirmed", "Unconfirmed"),
    ("warning", "Warning"),
    ("failed", "Failed"),
    ("skipped", "Skipped"),
]

SYNC_REASONS = [
    ("missing_barcode", "Missing Barcode"),
    ("duplicate_barcode", "Duplicate Barcode"),
    ("missing_name", "Missing Name"),
    ("inactive_product", "Inactive Product"),
    ("inactive_template", "Inactive Template"),
    ("api_error", "API Error"),
    ("validation_error", "Validation Error"),
]


class ProductProduct(models.Model):
    _inherit = "product.product"

    beyondid_sync_state = fields.Selection(
        SYNC_STATES,
        string="Beyond ID State",
        default="pending",
        index=True,
        copy=False,
    )
    beyondid_sync_reason = fields.Selection(
        SYNC_REASONS,
        string="Beyond ID Reason",
        index=True,
        copy=False,
    )
    beyondid_needs_sync = fields.Boolean(
        string="Needs Beyond ID Sync",
        default=True,
        index=True,
        copy=False,
    )
    beyondid_last_sync_date = fields.Datetime(
        string="Last Beyond ID Sync",
        readonly=True,
        copy=False,
    )
    beyondid_last_sync_attempt_date = fields.Datetime(
        string="Last Beyond ID Attempt",
        readonly=True,
        copy=False,
    )
    beyondid_last_payload_hash = fields.Char(
        string="Last Beyond ID Payload Hash",
        readonly=True,
        copy=False,
        index=True,
    )
    beyondid_last_code = fields.Char(
        string="Last Beyond ID Code",
        readonly=True,
        copy=False,
    )
    beyondid_external_productid = fields.Char(
        string="Beyond Product ID",
        readonly=True,
        copy=False,
        index=True,
    )
    beyondid_external_skuid = fields.Char(
        string="Beyond SKU ID",
        readonly=True,
        copy=False,
        index=True,
    )
    beyondid_last_warning = fields.Text(
        string="Last Beyond ID Warning",
        readonly=True,
        copy=False,
    )
    beyondid_last_error = fields.Text(
        string="Last Beyond ID Error",
        readonly=True,
        copy=False,
    )
    beyondid_last_operation = fields.Selection(
        [
            ("import", "Import"),
            ("delete", "Delete"),
            ("none", "None"),
        ],
        string="Last Beyond ID Operation",
        default="none",
        readonly=True,
        copy=False,
    )
    beyondid_last_run_id = fields.Many2one(
        "retailit.beyondid.product.sync.run",
        string="Last Beyond ID Run",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    beyondid_issue_count = fields.Integer(
        string="Beyond ID Issues",
        compute="_compute_beyondid_issue_count",
    )

    def _compute_beyondid_issue_count(self):
        grouped = self.env["retailit.beyondid.product.sync.issue"]._read_group(
            [("product_id", "in", self.ids)],
            ["product_id"],
            ["__count"],
        )
        counts = {
            product.id: count
            for product, count in grouped
        }
        for product in self:
            product.beyondid_issue_count = counts.get(product.id, 0)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._beyondid_mark_pending("New product variant")
        return records

    def write(self, values):
        tracked_fields = self._beyondid_tracked_variant_fields()
        relevant_change = bool(tracked_fields.intersection(values))
        result = super().write(values)
        if relevant_change and not self.env.context.get("skip_beyondid_mark_pending"):
            self.with_context(active_test=False)._beyondid_mark_pending("Product variant changed")
        return result

    @api.model
    def _beyondid_tracked_variant_fields(self):
        return {"barcode", "default_code", "active", "lst_price"}

    def _beyondid_mark_pending(self, message=False):
        products = self.filtered(lambda product: product.active and product.product_tmpl_id.active)
        if not products:
            return True
        duplicate_product_by_id = products._beyondid_duplicate_product_map()
        products = products.with_context(
            beyondid_duplicate_product_by_id=duplicate_product_by_id,
        )
        pending_products = self.env["product.product"]
        skipped_by_reason = {}
        for product in products:
            reason = product._beyondid_local_skip_reason_for_pending()
            if reason:
                skipped_by_reason.setdefault(reason, self.env["product.product"])
                skipped_by_reason[reason] |= product
            else:
                pending_products |= product

        for reason, skipped_products in skipped_by_reason.items():
            skipped_products.with_context(skip_beyondid_mark_pending=True).write({
                "beyondid_sync_state": "skipped",
                "beyondid_sync_reason": reason,
                "beyondid_needs_sync": False,
                "beyondid_last_error": False,
                "beyondid_last_warning": False,
            })

        if not pending_products:
            return True
        values = {
            "beyondid_sync_state": "pending",
            "beyondid_sync_reason": False,
            "beyondid_needs_sync": True,
            "beyondid_last_error": False,
        }
        if message:
            values["beyondid_last_warning"] = False
        pending_products.with_context(skip_beyondid_mark_pending=True).write(values)
        return True

    def _beyondid_local_skip_reason_for_pending(self):
        self.ensure_one()
        if not (self.barcode or "").strip() and not self._beyondid_has_remote_identity():
            return "missing_barcode"
        if self._beyondid_duplicate_barcode_product():
            return "duplicate_barcode"
        if not self._beyondid_display_name():
            return "missing_name"
        return False

    @api.model
    def _beyondid_cleanup_unsendable_pending_products(self):
        self.flush_model([
            "active",
            "barcode",
            "product_tmpl_id",
            "beyondid_sync_state",
            "beyondid_sync_reason",
            "beyondid_needs_sync",
            "beyondid_last_error",
            "beyondid_last_warning",
            "beyondid_last_payload_hash",
            "beyondid_external_productid",
            "beyondid_external_skuid",
            "beyondid_last_code",
        ])
        self.env["product.template"].flush_model(["active", "name"])
        self.env.cr.execute(
            """
            WITH active_products AS (
                SELECT
                    pp.id,
                    NULLIF(BTRIM(COALESCE(pp.barcode, '')), '') AS barcode,
                    NULLIF(BTRIM(COALESCE(pt.name->>'en_US', pt.name::text, '')), '') AS product_name,
                    (
                        NULLIF(BTRIM(COALESCE(pp.beyondid_last_payload_hash, '')), '') IS NOT NULL
                        OR NULLIF(BTRIM(COALESCE(pp.beyondid_external_productid, '')), '') IS NOT NULL
                        OR NULLIF(BTRIM(COALESCE(pp.beyondid_external_skuid, '')), '') IS NOT NULL
                        OR NULLIF(BTRIM(COALESCE(pp.beyondid_last_code, '')), '') IS NOT NULL
                    ) AS has_remote_identity
                FROM product_product pp
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE pp.active IS TRUE
                  AND pt.active IS TRUE
                  AND pp.beyondid_sync_state IN ('pending', 'failed', 'warning', 'skipped')
            ),
            duplicate_barcodes AS (
                SELECT barcode
                FROM active_products
                WHERE barcode IS NOT NULL
                GROUP BY barcode
                HAVING COUNT(*) > 1
            ),
            classified AS (
                SELECT
                    active_products.id,
                    CASE
                        WHEN active_products.barcode IS NULL
                         AND active_products.has_remote_identity IS FALSE
                            THEN 'missing_barcode'
                        WHEN active_products.barcode IS NOT NULL
                         AND duplicate_barcodes.barcode IS NOT NULL
                            THEN 'duplicate_barcode'
                        WHEN active_products.product_name IS NULL
                            THEN 'missing_name'
                        ELSE NULL
                    END AS reason
                FROM active_products
                LEFT JOIN duplicate_barcodes
                  ON duplicate_barcodes.barcode = active_products.barcode
            )
            UPDATE product_product pp
               SET beyondid_sync_state = 'skipped',
                   beyondid_sync_reason = classified.reason,
                   beyondid_needs_sync = FALSE,
                   beyondid_last_error = NULL,
                   beyondid_last_warning = NULL,
                   write_uid = %s,
                   write_date = (now() AT TIME ZONE 'UTC')
              FROM classified
             WHERE pp.id = classified.id
               AND classified.reason IS NOT NULL
            """,
            [self.env.uid],
        )
        self.invalidate_model([
            "beyondid_sync_state",
            "beyondid_sync_reason",
            "beyondid_needs_sync",
            "beyondid_last_error",
            "beyondid_last_warning",
            "write_uid",
            "write_date",
        ], flush=False)
        return True

    def _beyondid_product_key(self):
        self.ensure_one()
        product_id, sku_id = self._beyondid_remote_identity()
        return "%s|%s" % (product_id, sku_id)

    def _beyondid_remote_identity(self):
        self.ensure_one()
        product_id = self.beyondid_external_productid or str(self.id)
        sku_id = self.beyondid_external_skuid or str(self.id)
        return product_id, sku_id

    def _beyondid_has_remote_identity(self):
        self.ensure_one()
        return bool(
            self.beyondid_last_payload_hash
            or self.beyondid_external_productid
            or self.beyondid_external_skuid
            or self.beyondid_last_code
        )

    def _beyondid_display_name(self):
        self.ensure_one()
        return (self.display_name or self.name or "").strip()

    def _beyondid_sales_price(self):
        self.ensure_one()
        return float(self.lst_price or self.product_tmpl_id.list_price or 0.0)

    def _beyondid_round_money(self, value):
        return float(Decimal(str(value or 0.0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _beyondid_member_price(self):
        self.ensure_one()
        price = self._beyondid_round_money(self._beyondid_sales_price())
        return self._beyondid_round_money(price * 0.85)

    def _beyondid_payload_values(self):
        self.ensure_one()
        barcode = (self.barcode or "").strip()
        product_id, sku_id = self._beyondid_remote_identity()
        return {
            "itemtype": "sku",
            "productid": str(product_id),
            "skuid": str(sku_id),
            "code": str(barcode),
            "price": "%.2f" % self._beyondid_sales_price(),
            "name": self._beyondid_display_name(),
            "members": "%.2f" % self._beyondid_member_price(),
        }

    def _beyondid_delete_values(self):
        self.ensure_one()
        product_id, sku_id = self._beyondid_remote_identity()
        return {
            "itemtype": "sku",
            "productid": str(product_id),
            "skuid": str(sku_id),
            "code": str(self.beyondid_last_code or self.barcode or ""),
            "price": "%.2f" % self._beyondid_sales_price(),
            "name": self._beyondid_display_name() or str(product_id),
            "members": "%.2f" % self._beyondid_member_price(),
        }

    def _beyondid_payload_hash(self, values=None):
        self.ensure_one()
        values = values or self._beyondid_payload_values()
        payload = json.dumps(values, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _beyondid_duplicate_barcode_product(self):
        self.ensure_one()
        duplicate_product_by_id = self.env.context.get("beyondid_duplicate_product_by_id")
        if duplicate_product_by_id is not None:
            duplicate_id = duplicate_product_by_id.get(self.id)
            return self.browse(duplicate_id) if duplicate_id else self.env["product.product"]

        barcode = (self.barcode or "").strip()
        if not barcode:
            return self.env["product.product"]
        return self.with_context(active_test=False).search([
            ("id", "!=", self.id),
            ("barcode", "=", barcode),
            ("active", "=", True),
            ("product_tmpl_id.active", "=", True),
        ], limit=1)

    def _beyondid_duplicate_product_map(self):
        products = self.filtered(lambda product: product.barcode)
        if not products:
            return {}
        self.env.cr.execute(
            """
            SELECT source.id, MIN(duplicate.id)
              FROM product_product source
              JOIN product_product duplicate
                ON duplicate.barcode = source.barcode
               AND duplicate.id != source.id
              JOIN product_template duplicate_template
                ON duplicate_template.id = duplicate.product_tmpl_id
             WHERE source.id = ANY(%s)
               AND duplicate.active IS TRUE
               AND duplicate_template.active IS TRUE
             GROUP BY source.id
            """,
            [products.ids],
        )
        return dict(self.env.cr.fetchall())

    def _beyondid_prepare_sync_item(self):
        self.ensure_one()
        if not self.active:
            return {"action": "ignore", "reason": "inactive_product"}

        if not self.product_tmpl_id.active:
            return {"action": "ignore", "reason": "inactive_template"}

        if not (self.barcode or "").strip():
            if self._beyondid_has_remote_identity():
                return {
                    "action": "delete",
                    "reason": "missing_barcode",
                    "row": self._beyondid_delete_values(),
                    "hash": False,
                    "key": self._beyondid_product_key(),
                }
            return {"action": "skip", "reason": "missing_barcode"}

        if self._beyondid_duplicate_barcode_product():
            return {"action": "skip", "reason": "duplicate_barcode"}

        if not self._beyondid_display_name():
            return {"action": "skip", "reason": "missing_name"}

        row = self._beyondid_payload_values()
        payload_hash = self._beyondid_payload_hash(row)
        if (
            self.beyondid_last_payload_hash == payload_hash
            and self.beyondid_sync_state in ("synced", "warning")
            and not self.beyondid_needs_sync
        ):
            return {
                "action": "no_change",
                "reason": "no_change",
                "hash": payload_hash,
                "row": row,
                "key": self._beyondid_product_key(),
            }
        return {
            "action": "import",
            "reason": False,
            "row": row,
            "hash": payload_hash,
            "key": self._beyondid_product_key(),
            "update_only": self._beyondid_has_remote_identity(),
        }

    def _beyondid_sync_notification(self, run):
        message = _(
            "Beyond ID sync finished. Evaluated: %(evaluated)s, synced: %(synced)s, warnings: %(warnings)s, failed: %(failed)s, skipped: %(skipped)s, unchanged: %(unchanged)s."
        ) % {
            "evaluated": run.total_evaluated,
            "synced": run.total_synced,
            "warnings": run.total_warnings,
            "failed": run.total_failed,
            "skipped": run.total_skipped,
            "unchanged": run.total_no_changes,
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Beyond ID Product Sync"),
                "message": message,
                "type": "warning" if run.state == "warning" else ("danger" if run.state == "failed" else "success"),
                "sticky": run.state in ("warning", "failed"),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_beyondid_sync_selected(self):
        if len(self) > MANUAL_SYNC_LIMIT:
            raise UserError(_(
                "You selected %(count)s products. Manual sync supports up to %(limit)s products at a time. "
                "Please reduce the selection. Large updates are handled by the automatic sync."
            ) % {
                "count": len(self),
                "limit": MANUAL_SYNC_LIMIT,
            })
        return {
            "type": "ir.actions.client",
            "tag": "retailit_beyondid_product_sync.progress",
            "name": _("Beyond ID Product Sync"),
            "target": "current",
            "params": {
                "product_ids": self.with_context(active_test=False).ids,
            },
        }

    def action_beyondid_view_sync_history(self):
        action = self.env.ref("retailit_beyondid_product_sync.retailit_action_beyondid_product_sync_run").read()[0]
        action["target"] = "current"
        return action

    def action_beyondid_view_issues(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Beyond ID Issues"),
            "res_model": "retailit.beyondid.product.sync.issue",
            "view_mode": "list,form",
            "domain": [("product_id", "=", self.id)],
            "target": "current",
        }

    @api.model
    def _cron_beyondid_sync_pending(self):
        params = self.env["ir.config_parameter"].sudo()
        if params.get_param("retailit_beyondid_product_sync.auto_sync_enabled", "False") != "True":
            return False
        config = self.env["retailit.beyondid.api.client"]._get_config()
        if not config.get("enabled"):
            return False
        limit = params.get_param("retailit_beyondid_product_sync.cron_limit", "500")
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 500
        limit = max(1, min(limit, 5000))
        products = self.with_context(active_test=False).search([
            ("beyondid_needs_sync", "=", True),
            ("beyondid_sync_state", "in", ["pending", "failed", "warning"]),
            ("active", "=", True),
            ("product_tmpl_id.active", "=", True),
            ("barcode", "!=", False),
        ], limit=limit, order="write_date desc, id desc")
        if not products:
            products = self.with_context(active_test=False).search([
                ("beyondid_needs_sync", "=", True),
                ("beyondid_sync_state", "in", ["pending", "failed", "warning"]),
                ("active", "=", True),
                ("product_tmpl_id.active", "=", True),
            ], limit=limit, order="write_date desc, id desc")
        if not products:
            return False
        self.env["retailit.beyondid.product.sync.run"].create_and_execute(
            products,
            execution_type="cron",
            raise_on_error=False,
        )
        return True

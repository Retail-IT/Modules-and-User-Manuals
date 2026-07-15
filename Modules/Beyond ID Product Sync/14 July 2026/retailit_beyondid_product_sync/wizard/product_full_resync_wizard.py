import time

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BeyondIdProductFullResyncWizard(models.TransientModel):
    _name = "retailit.beyondid.product.full.resync.wizard"
    _description = "Beyond ID Product Full Resync Wizard"

    _RESET_PRODUCT_FIELDS = [
        "active",
        "barcode",
        "product_tmpl_id",
        "beyondid_sync_state",
        "beyondid_needs_sync",
        "beyondid_sync_reason",
        "beyondid_last_error",
        "beyondid_last_warning",
        "beyondid_last_payload_hash",
    ]
    _RESET_TEMPLATE_FIELDS = ["active", "name"]

    reset_scope = fields.Selection(
        [
            ("all", "All Products"),
            ("selected", "Selected Products"),
        ],
        string="Scope",
        readonly=True,
    )
    selected_product_count = fields.Integer(string="Selected Products", readonly=True)
    confirm_full_resync = fields.Boolean(
        string="I confirm that eligible products must be marked for full resync",
    )
    eligible_product_count = fields.Integer(string="Eligible Products", readonly=True)
    already_pending_count = fields.Integer(string="Already Pending", readonly=True)
    previously_synced_count = fields.Integer(string="Previously Synced", readonly=True)
    missing_barcode_count = fields.Integer(string="Missing Barcode", readonly=True)
    duplicate_barcode_count = fields.Integer(string="Duplicate Barcode", readonly=True)
    missing_name_count = fields.Integer(string="Missing Name", readonly=True)
    archived_product_count = fields.Integer(string="Archived Products", readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        values.update(self._get_reset_counts())
        return values

    @api.model
    def _is_selected_reset(self):
        return bool(
            self.env.context.get("reset_selected_only")
            and self.env.context.get("active_model") == "product.product"
        )

    @api.model
    def _get_selected_product_ids(self):
        if not self._is_selected_reset():
            return []
        active_ids = self.env.context.get("active_ids") or []
        if isinstance(active_ids, int):
            active_ids = [active_ids]
        return list(dict.fromkeys(int(product_id) for product_id in active_ids if product_id))

    @api.model
    def _get_reset_counts(self):
        selected_product_ids = self._get_selected_product_ids()
        selected_reset = self._is_selected_reset()
        self.env["product.product"].flush_model(self._RESET_PRODUCT_FIELDS)
        self.env["product.template"].flush_model(self._RESET_TEMPLATE_FIELDS)
        self.env.cr.execute(
            """
            WITH active_products AS (
                SELECT
                    pp.id,
                    NULLIF(BTRIM(COALESCE(pp.barcode, '')), '') AS barcode,
                    NULLIF(BTRIM(COALESCE(pt.name->>'en_US', pt.name::text, '')), '') AS product_name,
                    pp.beyondid_needs_sync,
                    pp.beyondid_sync_state,
                    pp.beyondid_last_payload_hash
                FROM product_product pp
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE pp.active IS TRUE
                  AND pt.active IS TRUE
            ),
            scoped_active_products AS (
                SELECT active_products.*
                FROM active_products
                WHERE NOT %s
                   OR active_products.id = ANY(%s::integer[])
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
                    active_products.*,
                    duplicate_barcodes.barcode IS NOT NULL AS has_duplicate_barcode,
                    active_products.barcode IS NOT NULL
                        AND active_products.product_name IS NOT NULL
                        AND duplicate_barcodes.barcode IS NULL AS is_eligible
                FROM scoped_active_products active_products
                LEFT JOIN duplicate_barcodes
                  ON duplicate_barcodes.barcode = active_products.barcode
            ),
            archived_products AS (
                SELECT COUNT(*) AS archived_count
                FROM product_product pp
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE (pp.active IS NOT TRUE
                   OR pt.active IS NOT TRUE)
                  AND (NOT %s OR pp.id = ANY(%s::integer[]))
            )
            SELECT
                COUNT(*) FILTER (WHERE is_eligible) AS eligible_product_count,
                COUNT(*) FILTER (WHERE is_eligible AND beyondid_needs_sync IS TRUE) AS already_pending_count,
                COUNT(*) FILTER (
                    WHERE is_eligible
                      AND (
                        beyondid_sync_state IN ('synced', 'warning')
                        OR NULLIF(BTRIM(COALESCE(beyondid_last_payload_hash, '')), '') IS NOT NULL
                      )
                ) AS previously_synced_count,
                COUNT(*) FILTER (WHERE barcode IS NULL) AS missing_barcode_count,
                COUNT(*) FILTER (WHERE barcode IS NOT NULL AND has_duplicate_barcode) AS duplicate_barcode_count,
                COUNT(*) FILTER (WHERE barcode IS NOT NULL AND product_name IS NULL) AS missing_name_count,
                (SELECT archived_count FROM archived_products) AS archived_product_count
            FROM classified
            """,
            [selected_reset, selected_product_ids, selected_reset, selected_product_ids],
        )
        row = self.env.cr.dictfetchone() or {}
        return {
            "reset_scope": "selected" if selected_reset else "all",
            "selected_product_count": len(selected_product_ids) if selected_reset else 0,
            "eligible_product_count": row.get("eligible_product_count") or 0,
            "already_pending_count": row.get("already_pending_count") or 0,
            "previously_synced_count": row.get("previously_synced_count") or 0,
            "missing_barcode_count": row.get("missing_barcode_count") or 0,
            "duplicate_barcode_count": row.get("duplicate_barcode_count") or 0,
            "missing_name_count": row.get("missing_name_count") or 0,
            "archived_product_count": row.get("archived_product_count") or 0,
        }

    def action_reset_products(self):
        self.ensure_one()
        if not self.confirm_full_resync:
            raise UserError(_("Please confirm the full resync reset before continuing."))
        selected_reset = self._is_selected_reset()
        selected_product_ids = self._get_selected_product_ids()
        if selected_reset and not selected_product_ids:
            raise UserError(_("Please select at least one product to reset."))

        counts = {
            "reset_scope": self.reset_scope,
            "selected_product_count": self.selected_product_count,
            "eligible_product_count": self.eligible_product_count,
            "missing_barcode_count": self.missing_barcode_count,
            "duplicate_barcode_count": self.duplicate_barcode_count,
            "missing_name_count": self.missing_name_count,
            "archived_product_count": self.archived_product_count,
        }
        started_at = time.monotonic()
        reset_count = self._reset_eligible_products()
        run = self.env["retailit.beyondid.product.sync.run"].create({
            "name": _("Beyond ID Selected Resync Reset") if selected_reset else _("Beyond ID Full Resync Reset"),
            "execution_type": "reset",
            "operation": "reset",
            "state": "done",
            "finished_at": fields.Datetime.now(),
            "duration": time.monotonic() - started_at,
            "total_evaluated": counts["eligible_product_count"],
            "total_reset": reset_count,
            "total_skipped": (
                counts["missing_barcode_count"]
                + counts["duplicate_barcode_count"]
                + counts["missing_name_count"]
                + counts["archived_product_count"]
            ),
        })
        action = self.env.ref("retailit_beyondid_product_sync.retailit_action_beyondid_product_sync").read()[0]
        action["context"] = {
            "search_default_needs_sync": 1,
        }
        if selected_reset:
            action["domain"] = [
                ("id", "in", selected_product_ids),
                ("active", "=", True),
                ("product_tmpl_id.active", "=", True),
            ]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Beyond ID Full Resync Reset"),
                "message": _(
                    "%(count)s products were marked as pending for full resync. History run: %(run)s."
                ) % {
                    "count": reset_count,
                    "run": run.display_name,
                },
                "type": "success",
                "sticky": False,
                "next": action,
            },
        }

    def _reset_eligible_products(self):
        Product = self.env["product.product"]
        selected_reset = self._is_selected_reset()
        selected_product_ids = self._get_selected_product_ids()
        self.env["product.template"].flush_model(self._RESET_TEMPLATE_FIELDS)
        Product.flush_model(self._RESET_PRODUCT_FIELDS)
        self.env.cr.execute(
            """
            WITH active_products AS (
                SELECT
                    pp.id,
                    NULLIF(BTRIM(COALESCE(pp.barcode, '')), '') AS barcode,
                    NULLIF(BTRIM(COALESCE(pt.name->>'en_US', pt.name::text, '')), '') AS product_name
                FROM product_product pp
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                WHERE pp.active IS TRUE
                  AND pt.active IS TRUE
            ),
            scoped_active_products AS (
                SELECT active_products.*
                FROM active_products
                WHERE NOT %s
                   OR active_products.id = ANY(%s::integer[])
            ),
            duplicate_barcodes AS (
                SELECT barcode
                FROM active_products
                WHERE barcode IS NOT NULL
                GROUP BY barcode
                HAVING COUNT(*) > 1
            ),
            eligible_products AS (
                SELECT scoped_active_products.id
                FROM scoped_active_products
                LEFT JOIN duplicate_barcodes
                  ON duplicate_barcodes.barcode = scoped_active_products.barcode
                WHERE scoped_active_products.barcode IS NOT NULL
                  AND scoped_active_products.product_name IS NOT NULL
                  AND duplicate_barcodes.barcode IS NULL
            )
            UPDATE product_product pp
               SET beyondid_sync_state = 'pending',
                   beyondid_needs_sync = TRUE,
                   beyondid_sync_reason = NULL,
                   beyondid_last_error = NULL,
                   beyondid_last_warning = NULL,
                   beyondid_last_payload_hash = NULL,
                   write_uid = %s,
                   write_date = (now() AT TIME ZONE 'UTC')
              FROM eligible_products
             WHERE pp.id = eligible_products.id
            RETURNING pp.id
            """,
            [selected_reset, selected_product_ids, self.env.uid],
        )
        reset_count = len(self.env.cr.fetchall())
        Product.invalidate_model([
            "beyondid_sync_state",
            "beyondid_needs_sync",
            "beyondid_sync_reason",
            "beyondid_last_error",
            "beyondid_last_warning",
            "beyondid_last_payload_hash",
            "write_uid",
            "write_date",
        ], flush=False)
        return reset_count

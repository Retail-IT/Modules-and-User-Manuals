from datetime import datetime, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError


class RetailitStockInventoryCloudImportWizard(models.TransientModel):
    _name = "retailit.stock.inventory.cloud.import.wizard"
    _description = "Import Inventory from Beyond ID"

    inventory_id = fields.Many2one(
        comodel_name="retailit.stock.inventory",
        string="Inventory",
        required=True,
        readonly=True,
    )
    shop_code = fields.Selection(
        selection="_selection_shop_code",
        string="Beyond ID Shop",
    )
    note = fields.Text(string="Note / Comment", readonly=True)
    raw_reference = fields.Char(string="Cloud Reference", readonly=True)
    count_line_ids = fields.One2many(
        comodel_name="retailit.stock.inventory.cloud.count.line",
        inverse_name="wizard_id",
        string="Available Inventories",
    )
    selected_count_id = fields.Many2one(
        comodel_name="retailit.stock.inventory.cloud.count.line",
        string="Inventory Count",
        domain="[('wizard_id', '=', id)]",
    )
    count_line_count = fields.Integer(compute="_compute_count_line_count")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "shop_code" in fields_list and not values.get("shop_code"):
            options = self._selection_shop_code()
            if options:
                values["shop_code"] = options[0][0]
        return values

    @api.depends("count_line_ids")
    def _compute_count_line_count(self):
        for wizard in self:
            wizard.count_line_count = len(wizard.count_line_ids)

    @api.model
    def _selection_shop_code(self):
        shops = self.env["retailit.beyondid.api.client"].list_shops()
        options = []
        for shop in shops:
            code = shop.get("code")
            if not code:
                continue
            name = shop.get("name") or code
            area = shop.get("area") or shop.get("deviceArea")
            label = name if name == code else "%s (%s)" % (name, code)
            if area:
                label = "%s - %s" % (label, area)
            options.append((code, label))
        return options

    def _extract_cloud_rows(self, payload):
        data = payload.get("data")
        if data is None:
            data = payload.get("items") or payload.get("stock") or payload.get("stocks") or payload.get("inventory") or []
        if isinstance(data, dict):
            data = data.get("items") or data.get("stock") or data.get("stocks") or []
        if not isinstance(data, list):
            raise UserError(self.env._("Beyond ID returned stock data in an unsupported format."))

        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            code = (
                item.get("code")
                or item.get("barcode")
                or item.get("Barcode")
                or item.get("product")
                or item.get("productCode")
            )
            qty = (
                item.get("count")
                if item.get("count") is not None
                else item.get("qty")
                if item.get("qty") is not None
                else item.get("quantity")
                if item.get("quantity") is not None
                else item.get("stock")
            )
            name = item.get("name") or item.get("productName") or item.get("description")
            if code in (None, ""):
                continue
            rows.append((code, qty if qty is not None else 0, name))
        return rows

    def _get_payload_note(self, payload):
        return (
            payload.get("note")
            or payload.get("comment")
            or payload.get("comments")
            or payload.get("description")
            or payload.get("message")
            or ""
        )

    def _get_payload_reference(self, payload):
        properties = payload.get("properties") or {}
        return (
            properties.get("code")
            or payload.get("code")
            or payload.get("inventoryCode")
            or payload.get("resultid")
            or payload.get("id")
            or ""
        )

    def _timestamp_to_datetime(self, timestamp):
        if not timestamp:
            return False
        try:
            value = datetime.fromtimestamp(float(timestamp) / 1000.0, timezone.utc).replace(tzinfo=None)
            return fields.Datetime.to_string(value)
        except (TypeError, ValueError, OverflowError):
            return False

    def _get_selected_count(self):
        self.ensure_one()
        selected_count = self.count_line_ids.filtered("is_selected")
        if not selected_count:
            raise UserError(self.env._("Please search and select a Beyond ID inventory count."))
        if len(selected_count) > 1:
            raise UserError(self.env._("Please select only one Beyond ID inventory count."))
        return selected_count

    def action_search_inventories(self):
        self.ensure_one()
        if not self.shop_code:
            raise UserError(self.env._("Please select a Beyond ID shop."))
        inventories = self.env["retailit.beyondid.api.client"].search_inventories(self.shop_code, inventory_type="UPLOAD")
        self.count_line_ids.unlink()
        self.write({
            "selected_count_id": False,
            "note": False,
            "raw_reference": False,
        })
        count_values = []
        for inventory in inventories:
            code = inventory.get("code")
            if not code:
                continue
            timestamp = inventory.get("timestamp")
            count_values.append({
                "wizard_id": self.id,
                "code": code,
                "reference": inventory.get("reference"),
                "shop_code": inventory.get("shop") or self.shop_code,
                "inventory_type": inventory.get("type") or "UPLOAD",
                "zone": inventory.get("zone"),
                "timestamp_ms": str(timestamp or ""),
                "inventory_datetime": self._timestamp_to_datetime(timestamp),
                "number_of_eans": inventory.get("numberOfEans") or 0,
                "number_of_epcs": inventory.get("numberOfEpcs") or 0,
            })
        created_lines = self.env["retailit.stock.inventory.cloud.count.line"].create(count_values)
        if len(created_lines) == 1:
            created_lines.is_selected = True
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Import from Beyond ID"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_import(self):
        self.ensure_one()
        if self.inventory_id.state != "draft":
            raise UserError(self.env._("Cloud import is only available in draft inventories."))
        if not self.shop_code:
            raise UserError(self.env._("Please select a Beyond ID shop."))
        selected_count = self._get_selected_count()
        self.selected_count_id = selected_count
        payload = self.env["retailit.beyondid.api.client"].download_inventory_by_code(
            self.shop_code,
            selected_count.code,
            inventory_type=selected_count.inventory_type or "UPLOAD",
            mode="sku",
        )
        rows = self._extract_cloud_rows(payload)
        result = self.inventory_id._import_rfid_rows(rows)
        if not result["line_count"] and not result["missing_barcode_count"]:
            raise UserError(self.env._("Beyond ID did not return any importable barcode rows for the selected inventory."))
        self.inventory_id.write({"inventory_type": "cloud"})
        self.inventory_id.with_context(skip_cloud_theoretical_qty_update=True).action_start()
        self.write({
            "note": self._get_payload_note(payload) or selected_count.reference,
            "raw_reference": self._get_payload_reference(payload) or selected_count.code,
        })
        message = self.env._(
            "Cloud inventory imported and started. %(lines)s product lines imported and %(missing)s missing barcodes registered."
        ) % {
            "lines": result["line_count"],
            "missing": result["missing_barcode_count"],
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Beyond ID"),
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "name": self.inventory_id.display_name,
                    "res_model": "retailit.stock.inventory",
                    "res_id": self.inventory_id.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }


class RetailitStockInventoryCloudCountLine(models.TransientModel):
    _name = "retailit.stock.inventory.cloud.count.line"
    _description = "Beyond ID Available Inventory Count"
    _rec_name = "code"
    _order = "inventory_datetime desc, id desc"

    wizard_id = fields.Many2one(
        comodel_name="retailit.stock.inventory.cloud.import.wizard",
        required=True,
        ondelete="cascade",
    )
    code = fields.Char(required=True, readonly=True)
    reference = fields.Char(readonly=True)
    shop_code = fields.Char(readonly=True)
    inventory_type = fields.Char(string="Type", readonly=True)
    zone = fields.Char(readonly=True)
    timestamp_ms = fields.Char(string="Timestamp", readonly=True)
    inventory_datetime = fields.Datetime(string="Date", readonly=True)
    number_of_eans = fields.Integer(string="Barcodes/EANs", readonly=True)
    number_of_epcs = fields.Integer(string="EPCs", readonly=True)
    is_selected = fields.Boolean(string="Select")

    def write(self, vals):
        result = super().write(vals)
        if vals.get("is_selected"):
            for line in self:
                line.wizard_id.count_line_ids.filtered(lambda count: count != line and count.is_selected).write({
                    "is_selected": False,
                })
                line.wizard_id.selected_count_id = line
                line.wizard_id.write({
                    "note": False,
                    "raw_reference": False,
                })
        elif "is_selected" in vals:
            for wizard in self.mapped("wizard_id"):
                selected = wizard.count_line_ids.filtered("is_selected")
                wizard.selected_count_id = selected[:1] if selected else False
        return result

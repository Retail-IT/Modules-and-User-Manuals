import base64
import csv
import io
from collections import defaultdict
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

try:
    import openpyxl
except ImportError:
    openpyxl = None


class RetailitStockInventory(models.Model):
    _name = "retailit.stock.inventory"
    _description = "Inventory Adjustment"
    _order = "date desc, id desc"
    _rfid_import_batch_size = 1000
    _scanner_barcode_max_length = 64

    name = fields.Char(string="Reference", required=True, default="New")
    date = fields.Datetime(string="Date", default=fields.Datetime.now, required=True)
    inventory_date = fields.Date(string="Counting Date", default=fields.Date.context_today)
    state = fields.Selection([("draft", "Draft"),("in_progress", "In Progress"),("done", "Validated"),("cancel", "Cancelled")], default="draft", readonly=True, copy=False, index=True, string="State")
    location_id = fields.Many2one(comodel_name="stock.location", string="Location", required=True, domain="[('usage', '=', 'internal')]")
    company_id = fields.Many2one(comodel_name="res.company", string="Company", default=lambda self: self.env.company, required=True)
    count_mode = fields.Selection([
        ("cycle", "Cycle Count"),
        ("full", "Full Count"),
    ], string="Count Mode", default="cycle", required=True)
    analytic_account_id = fields.Many2one(
        comodel_name="account.analytic.account",
        string="Analytic Account",
        required=True,
        check_company=True,
    )
    inventory_type = fields.Selection([("all", "All Products"),("partial", "Selected Products"),("template", "Product Templates"),("category", "Product Category"),("lot", "Lot/Serial"),("manual", "Manual Selection"), ("import", "Import from Excel"), ("cloud", "Import from Cloud")], default="all", string="Inventory Type", required=True)
    product_template_ids = fields.Many2many(comodel_name="product.template", string="Product Templates")
    product_ids = fields.Many2many(comodel_name="product.product", string="Products")
    categ_id = fields.Many2one(comodel_name="product.category", string="Category")
    lot_id = fields.Many2one(comodel_name="stock.lot", string="Lot/Serial")
    include_exhausted = fields.Boolean(string="Include Exhausted Products", default=False)
    prefill_counted = fields.Boolean(string="Prefill Counted Quantity", default=True)
    import_file = fields.Binary(string="Excel File")
    import_filename = fields.Char(string="File Name")
    line_ids = fields.One2many(comodel_name="retailit.stock.inventory.line", inverse_name="inventory_id", string="Lines")
    visible_line_ids = fields.One2many(
        comodel_name="retailit.stock.inventory.line",
        inverse_name="inventory_id",
        string="Visible Lines",
        domain=["|", ("count_source", "!=", "system"), ("is_counted", "=", True)],
    )
    missing_barcode_ids = fields.One2many(comodel_name="retailit.stock.inventory.missing.barcode", inverse_name="inventory_id", string="Missing Barcodes")
    missed_item_ids = fields.One2many(comodel_name="retailit.stock.inventory.missed.item", inverse_name="inventory_id", string="Missed Items")
    scan_event_ids = fields.One2many(comodel_name="retailit.stock.inventory.scan.event", inverse_name="inventory_id", string="Scan Logs")
    move_ids = fields.One2many(comodel_name="stock.move", inverse_name="retailit_stock_inventory_id", string="Moves", readonly=True)
    line_count = fields.Integer(compute="_compute_counts", string="# Lines")
    shortage_count = fields.Integer(compute="_compute_counts", string="# Shortages")
    surplus_count = fields.Integer(compute="_compute_counts", string="# Surpluses")
    missing_barcode_count = fields.Integer(compute="_compute_counts", string="# Missing Barcodes")
    missed_item_count = fields.Integer(compute="_compute_counts", string="# Missed Items")
    shortage_ids = fields.One2many("retailit.stock.inventory.line", "inventory_id", compute="_compute_shortage_surplus", string="Shortages")
    surplus_ids = fields.One2many("retailit.stock.inventory.line", "inventory_id", compute="_compute_shortage_surplus", string="Surpluses")

    @api.depends(
        "line_ids",
        "line_ids.difference_qty",
        "line_ids.count_source",
        "line_ids.is_counted",
        "missing_barcode_ids",
        "missed_item_ids",
    )
    def _compute_counts(self):
        for inv in self:
            visible_lines = inv.line_ids.filtered(lambda line: not line._is_system_missed_line())
            inv.line_count = len(visible_lines)
            variance_lines = inv.line_ids if inv.count_mode == "full" else inv.line_ids.filtered(lambda l: l.is_counted)
            inv.shortage_count = len(variance_lines.filtered(lambda l: l.difference_qty < 0))
            inv.surplus_count = len(variance_lines.filtered(lambda l: l.difference_qty > 0))
            inv.missing_barcode_count = len(inv.missing_barcode_ids)
            inv.missed_item_count = len(inv.missed_item_ids)

    @api.depends("line_ids.difference_qty")
    def _compute_shortage_surplus(self):
        for inv in self:
            lines = inv.line_ids if inv.count_mode == "full" else inv.line_ids.filtered(lambda l: l.is_counted)
            inv.shortage_ids = lines.filtered(lambda l: l.difference_qty < 0)
            inv.surplus_ids = lines.filtered(lambda l: l.difference_qty > 0)

    @api.onchange("inventory_type")
    def _onchange_inventory_type(self):
        self.product_ids = False
        self.product_template_ids = False
        self.categ_id = False
        self.lot_id = False
        self.import_file = False

    def action_start(self):
        """Start inventory and generate lines."""
        Inventory = self.env[self._name]
        if Inventory.search_count([('state', '=', 'in_progress'), ('id', 'not in', self.ids), ('location_id', '=', self.location_id.id)]):
            raise UserError(self.env._("An inventory is already in progress for the selected location. Complete it before starting another."))
        for inv in self:
            if inv.state != "draft":
                raise UserError(self.env._("Only draft inventories can be started."))         
            if inv.inventory_type == "import" and inv.import_file:
                inv._import_from_excel()
            elif inv.inventory_type == "cloud":
                if not inv.line_ids and not inv.missing_barcode_ids:
                    raise UserError(self.env._("Please import inventory data from Beyond ID before starting this cloud inventory."))
                if not self.env.context.get("skip_cloud_theoretical_qty_update"):
                    inv._update_theoretical_qty()
            elif inv.inventory_type == "manual":
                inv._update_theoretical_qty()
            else:
                inv._generate_lines()

            inv._refresh_missed_items()
            inv.state = "in_progress"
    
    def _update_theoretical_qty(self):
        """Update theoretical quantity for manual lines."""
        self.ensure_one()
        lines = self.line_ids
        if not lines:
            return

        products_by_location_lot = defaultdict(set)
        for line in lines:
            lot_id = line.lot_id.id if line.lot_id else False
            products_by_location_lot[(line.location_id.id, lot_id)].add(line.product_id.id)

        theoretical_by_key = {}
        Quant = self.env["stock.quant"]
        for (location_id, lot_id), product_ids in products_by_location_lot.items():
            for product_id_batch in self._chunked(list(product_ids)):
                groups = Quant._read_group([
                    ("product_id", "in", product_id_batch),
                    ("location_id", "=", location_id),
                    ("lot_id", "=", lot_id),
                ], ["product_id"], ["quantity:sum"])
                for product, quantity in groups:
                    if product:
                        theoretical_by_key[(product.id, location_id, lot_id)] = quantity

        for line in lines:
            lot_id = line.lot_id.id if line.lot_id else False
            line.theoretical_qty = theoretical_by_key.get((line.product_id.id, line.location_id.id, lot_id), 0.0)

    def _generate_lines(self):
        """Generate lines from quants based on inventory type."""
        self.ensure_one()
        self.line_ids.unlink()
        
        domain = [("location_id", "child_of", self.location_id.id)]
        
        if not self.include_exhausted:
            domain.append(("quantity", "!=", 0))
        
        if self.inventory_type == "partial" and self.product_ids:
            domain.append(("product_id", "in", self.product_ids.ids))
        elif self.inventory_type == "category" and self.categ_id:
            domain.append(("product_id.categ_id", "child_of", self.categ_id.id))
        elif self.inventory_type == "lot" and self.lot_id:
            domain.append(("lot_id", "=", self.lot_id.id))
        elif self.inventory_type == "template" and self.product_template_ids:
            # Get all product.product from selected templates
            product_ids = self.env["product.product"].search([
                ("product_tmpl_id", "in", self.product_template_ids.ids)
            ]).ids
            domain.append(("product_id", "in", product_ids))
        
        quants = self.env["stock.quant"].search(domain)
        
        vals_list = []
        for quant in quants:
            if not quant.product_id.is_storable:
                continue
            vals_list.append({
                "inventory_id": self.id,
                "product_id": quant.product_id.id,
                "location_id": quant.location_id.id,
                "lot_id": quant.lot_id.id,
                "package_id": quant.package_id.id,
                "owner_id": quant.owner_id.id,
                "theoretical_qty": quant.quantity,
                "product_qty": quant.quantity if self.prefill_counted else 0,
            })
        
        if self.include_exhausted and self.inventory_type in ("all", "partial", "category", "template"):
            product_domain = [("is_storable", "=", True)]
            if self.inventory_type == "partial" and self.product_ids:
                product_domain.append(("id", "in", self.product_ids.ids))
            elif self.inventory_type == "category" and self.categ_id:
                product_domain.append(("categ_id", "child_of", self.categ_id.id))
            elif self.inventory_type == "template" and self.product_template_ids:
                product_domain.append(("product_tmpl_id", "in", self.product_template_ids.ids))
            
            products = self.env["product.product"].search(product_domain)
            existing_products = {(v["product_id"], v.get("lot_id", False)) for v in vals_list}
            
            for product in products:
                if (product.id, False) not in existing_products:
                    vals_list.append({
                        "inventory_id": self.id,
                        "product_id": product.id,
                        "location_id": self.location_id.id,
                        "theoretical_qty": 0,
                        "product_qty": 0,
                    })
        
        if vals_list:
            self.env["retailit.stock.inventory.line"].create(vals_list)

    def _chunked(self, values, size=None):
        size = size or self._rfid_import_batch_size
        for index in range(0, len(values), size):
            yield values[index:index + size]

    def _normalize_rfid_code(self, value):
        """Return a barcode-safe text value without losing leading zeroes."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value.is_integer():
                return str(int(value))
            return str(value).strip()
        return str(value).strip()

    def _normalize_scanned_barcode(self, value):
        code = self._normalize_rfid_code(value)
        if not code:
            raise UserError(self.env._("Barcode is required."))
        if len(code) > self._scanner_barcode_max_length or any(char.isspace() for char in code):
            raise UserError(self.env._("Invalid barcode scan. Please scan a barcode value only."))
        return code

    def _parse_rfid_qty(self, value, code):
        if value in (None, ""):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            raise UserError(self.env._("Invalid count value for barcode %(code)s: %(value)s") % {
                "code": code,
                "value": value,
            })

    def _read_rfid_import_rows(self, file_content):
        """Read RFID file rows from the first sheet preserving barcode text."""
        filename = (self.import_filename or "").lower()
        if filename.endswith(".csv"):
            stream = io.StringIO(file_content.decode("utf-8-sig"))
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                return
            field_map = {field.strip().lower(): field for field in reader.fieldnames if field}
            if "code" not in field_map or "count" not in field_map:
                raise UserError(self.env._("The RFID file must contain code and count columns."))
            for row in reader:
                product_name = row.get(field_map["name"]) if "name" in field_map else None
                yield row.get(field_map["code"]), row.get(field_map["count"]), product_name
            return

        if not openpyxl:
            raise UserError(self.env._("openpyxl library not installed."))

        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(file_content),
                read_only=True,
                data_only=True,
            )
        except Exception as e:
            raise UserError(self.env._("Error reading file: %s") % str(e))

        try:
            sheet = workbook.worksheets[0]
            rows = sheet.iter_rows(values_only=True)
            try:
                headers = next(rows)
            except StopIteration:
                return

            header_map = {
                str(header).strip().lower(): index
                for index, header in enumerate(headers)
                if header not in (None, "")
            }
            if "code" not in header_map or "count" not in header_map:
                raise UserError(self.env._("The RFID file must contain code and count columns."))

            code_index = header_map["code"]
            count_index = header_map["count"]
            name_index = header_map.get("name")
            for row in rows:
                if not row or not any(value not in (None, "") for value in row):
                    continue
                code = row[code_index] if code_index < len(row) else None
                qty = row[count_index] if count_index < len(row) else None
                product_name = row[name_index] if name_index is not None and name_index < len(row) else None
                yield code, qty, product_name
        finally:
            workbook.close()

    def _import_rfid_rows(self, rows, clear_existing=True):
        """Import RFID rows by barcode in batches from any source."""
        self.ensure_one()
        if clear_existing:
            self.line_ids.unlink()
            self.missing_barcode_ids.unlink()

        qty_by_code = defaultdict(float)
        name_by_code = {}
        occurrence_count_by_code = defaultdict(int)
        for raw_code, raw_qty, raw_product_name in rows:
            code = self._normalize_rfid_code(raw_code)
            if not code:
                continue
            qty_by_code[code] += self._parse_rfid_qty(raw_qty, code)
            occurrence_count_by_code[code] += 1
            if code not in name_by_code and raw_product_name not in (None, ""):
                name_by_code[code] = str(raw_product_name).strip()

        if not qty_by_code:
            return {
                "line_count": 0,
                "missing_barcode_count": 0,
            }

        Product = self.env["product.product"]
        InventoryLine = self.env["retailit.stock.inventory.line"]
        MissingBarcode = self.env["retailit.stock.inventory.missing.barcode"]
        imported_line_count = 0
        missing_barcode_count = 0
        codes = list(qty_by_code)
        for code_batch in self._chunked(codes):
            product_rows = Product.search_read(
                [("barcode", "in", code_batch)],
                ["id", "barcode"],
            )
            product_id_by_barcode = {}
            for product_row in product_rows:
                barcode = product_row.get("barcode")
                if barcode and barcode not in product_id_by_barcode:
                    product_id_by_barcode[barcode] = product_row["id"]

            product_ids = list(product_id_by_barcode.values())
            theoretical_qty_by_product = self._get_theoretical_qty_by_product(product_ids)

            vals_list = []
            missing_vals_list = []
            for code in code_batch:
                product_id = product_id_by_barcode.get(code)
                if not product_id:
                    missing_vals_list.append({
                        "inventory_id": self.id,
                        "barcode": code,
                        "product_name": name_by_code.get(code),
                        "qty": qty_by_code[code],
                        "source": "rfid",
                        "occurrence_count": occurrence_count_by_code[code],
                    })
                    continue
                vals_list.append({
                    "inventory_id": self.id,
                    "product_id": product_id,
                    "location_id": self.location_id.id,
                    "lot_id": False,
                    "theoretical_qty": theoretical_qty_by_product.get(product_id, 0.0),
                    "product_qty": qty_by_code[code],
                    "count_source": "rfid",
                    "is_counted": True,
                })

            if vals_list:
                InventoryLine.create(vals_list)
                imported_line_count += len(vals_list)
            if missing_vals_list:
                MissingBarcode.create(missing_vals_list)
                missing_barcode_count += len(missing_vals_list)

        return {
            "line_count": imported_line_count,
            "missing_barcode_count": missing_barcode_count,
        }

    def _get_theoretical_qty_by_product(self, product_ids):
        theoretical_qty_by_product = {}
        Quant = self.env["stock.quant"]
        for product_id_batch in self._chunked(product_ids):
            groups = Quant._read_group([
                ("product_id", "in", product_id_batch),
                ("location_id", "=", self.location_id.id),
                ("lot_id", "=", False),
            ], ["product_id"], ["quantity:sum"])
            for product, quantity in groups:
                if product:
                    theoretical_qty_by_product[product.id] = quantity
        return theoretical_qty_by_product

    def _get_theoretical_qty(self, product_id):
        return self._get_theoretical_qty_by_product([product_id]).get(product_id, 0.0)

    def _refresh_missed_items(self):
        """Rebuild full-count coverage report without creating stock adjustments."""
        MissedItem = self.env["retailit.stock.inventory.missed.item"]
        InventoryLine = self.env["retailit.stock.inventory.line"]
        Quant = self.env["stock.quant"]
        for inv in self:
            existing_missed_by_product = {
                missed.product_id.id: {
                    "inventory_id": inv.id,
                    "product_id": missed.product_id.id,
                    "barcode": missed.barcode,
                    "location_id": missed.location_id.id,
                    "theoretical_qty": missed.theoretical_qty,
                }
                for missed in inv.missed_item_ids
            }
            inv.missed_item_ids.unlink()
            if inv.count_mode != "full":
                continue

            counted_product_ids = set(InventoryLine.search([
                ("inventory_id", "=", inv.id),
                ("is_counted", "=", True),
            ]).product_id.ids)
            groups = Quant._read_group([
                ("location_id", "=", inv.location_id.id),
                ("lot_id", "=", False),
            ], ["product_id"], ["quantity:sum"])

            missed_by_product = dict(existing_missed_by_product)
            for product, quantity in groups:
                if not product or not product.is_storable or product.id in counted_product_ids or quantity == 0:
                    continue
                missed_by_product[product.id] = {
                    "inventory_id": inv.id,
                    "product_id": product.id,
                    "barcode": product.barcode,
                    "location_id": inv.location_id.id,
                    "theoretical_qty": quantity,
                }

            vals_list = [
                vals
                for product_id, vals in missed_by_product.items()
                if product_id not in counted_product_ids and vals["theoretical_qty"] != 0
            ]

            if vals_list:
                MissedItem.create(vals_list)
            inv._sync_full_count_missed_shortage_lines(vals_list)

    def _sync_full_count_missed_shortage_lines(self, missed_vals_list):
        """Mirror missed full-count products as shortage lines with counted qty zero."""
        self.ensure_one()
        InventoryLine = self.env["retailit.stock.inventory.line"]
        missed_by_product = {
            vals["product_id"]: vals
            for vals in missed_vals_list
            if vals["theoretical_qty"] != 0
        }
        missed_product_ids = set(missed_by_product)
        system_lines = InventoryLine.search([
            ("inventory_id", "=", self.id),
            ("count_source", "=", "system"),
            ("is_counted", "=", False),
        ])
        lines_to_remove = system_lines.filtered(lambda line: line.product_id.id not in missed_product_ids)
        if lines_to_remove:
            lines_to_remove.with_context(skip_missed_refresh=True).unlink()

        existing_lines = InventoryLine.search([
            ("inventory_id", "=", self.id),
            ("product_id", "in", list(missed_product_ids)),
            ("is_counted", "=", False),
        ])
        existing_by_product = {line.product_id.id: line for line in existing_lines}

        create_vals = []
        for product_id, vals in missed_by_product.items():
            line_vals = {
                "location_id": vals["location_id"],
                "theoretical_qty": vals["theoretical_qty"],
                "product_qty": 0.0,
                "scan_qty": 0.0,
                "count_source": "system",
                "is_counted": False,
                "was_scanned": False,
            }
            line = existing_by_product.get(product_id)
            if line:
                line.with_context(skip_manual_count_on_qty_write=True).write(line_vals)
            else:
                create_vals.append({
                    **line_vals,
                    "inventory_id": self.id,
                    "product_id": product_id,
                    "lot_id": False,
                })
        if create_vals:
            InventoryLine.create(create_vals)

    def _remove_missed_item(self, product_id):
        if self.count_mode != "full" or not product_id:
            return
        self.env["retailit.stock.inventory.missed.item"].search([
            ("inventory_id", "=", self.id),
            ("product_id", "=", product_id),
        ]).unlink()

    def _log_scan_event(self, **values):
        values.setdefault("inventory_id", self.id)
        values.setdefault("user_id", self.env.user.id)
        return self.env["retailit.stock.inventory.scan.event"].create(values)

    def _get_or_create_missing_barcode(self, barcode, qty=0.0, source="scan", product_name=False):
        missing = self.env["retailit.stock.inventory.missing.barcode"].search([
            ("inventory_id", "=", self.id),
            ("barcode", "=", barcode),
        ], limit=1)
        if missing:
            vals = {"occurrence_count": missing.occurrence_count + 1}
            if qty:
                vals["qty"] = missing.qty + qty
            if product_name and not missing.product_name:
                vals["product_name"] = product_name
            if missing.source != source:
                vals["source"] = "mixed"
            missing.write(vals)
            return missing
        return self.env["retailit.stock.inventory.missing.barcode"].create({
            "inventory_id": self.id,
            "barcode": barcode,
            "product_name": product_name,
            "qty": qty,
            "source": source,
        })

    def _scan_payload(self, barcode, match_source, product=False, line=False, message=False):
        return {
            "barcode": barcode,
            "match_source": match_source,
            "message": message or "",
            "can_save": match_source in ("inventory_item", "product_product"),
            "line_id": line.id if line else False,
            "product_id": product.id if product else False,
            "product_name": product.display_name if product else "",
            "location_name": self.location_id.display_name,
            "theoretical_qty": line.theoretical_qty if line else (self._get_theoretical_qty(product.id) if product else 0.0),
            "current_qty": line.product_qty if line else 0.0,
        }

    def action_open_scan(self, barcode=False):
        self.ensure_one()
        if self.state != "in_progress":
            raise UserError(self.env._("Scanning is only available for in-progress inventories."))
        default_barcode = self._normalize_scanned_barcode(barcode) if barcode else False
        default_scan_result = self.action_scan_barcode(default_barcode) if default_barcode else False
        continuous_scan = not bool(default_barcode)
        return {
            "type": "ir.actions.client",
            "tag": "retailit_stock_inventory_adjustment.scan_inventory",
            "target": "new",
            "name": self.env._("Scan Inventory"),
            "context": {
                "active_id": self.id,
                "inventory_name": self.name,
                "location_name": self.location_id.display_name,
                "default_barcode": default_barcode,
                "default_scan_result": default_scan_result,
                "continuous_scan": continuous_scan,
            },
        }

    def action_scan_barcode(self, barcode):
        self.ensure_one()
        if self.state != "in_progress":
            raise UserError(self.env._("Scanning is only available for in-progress inventories."))
        code = self._normalize_scanned_barcode(barcode)

        line = self.env["retailit.stock.inventory.line"].search([
            ("inventory_id", "=", self.id),
            ("product_id.barcode", "=", code),
        ], limit=1)
        if line:
            self._log_scan_event(
                event_type="lookup",
                match_source="inventory_item",
                line_id=line.id,
                product_id=line.product_id.id,
                barcode=code,
                previous_qty=line.product_qty,
                message="Found in inventory items",
            )
            return self._scan_payload(code, "inventory_item", line.product_id, line)

        product = self.env["product.product"].search([("barcode", "=", code)], limit=1)
        if product:
            self._log_scan_event(
                event_type="lookup",
                match_source="product_product",
                product_id=product.id,
                barcode=code,
                message="Found in product master",
            )
            return self._scan_payload(code, "product_product", product, False)

        self._log_scan_event(
            event_type="missing",
            match_source="missing",
            barcode=code,
            message="Barcode not found",
        )
        return self._scan_payload(code, "missing", False, False, self.env._("Barcode not found in Odoo."))

    def action_apply_scan_qty(self, barcode, qty):
        self.ensure_one()
        if self.state != "in_progress":
            raise UserError(self.env._("Scanning is only available for in-progress inventories."))
        code = self._normalize_scanned_barcode(barcode)
        try:
            new_qty = float(qty)
        except (TypeError, ValueError):
            raise UserError(self.env._("Quantity must be a valid number."))
        if new_qty < 0:
            raise UserError(self.env._("Quantity cannot be negative."))

        self.env.cr.execute("SELECT id FROM retailit_stock_inventory WHERE id = %s FOR UPDATE", [self.id])
        line = self.env["retailit.stock.inventory.line"].search([
            ("inventory_id", "=", self.id),
            ("product_id.barcode", "=", code),
        ], limit=1)

        if line:
            self.env.cr.execute("SELECT id FROM retailit_stock_inventory_line WHERE id = %s FOR UPDATE", [line.id])
            previous_qty = line.product_qty
            line.write({
                "product_qty": new_qty,
                "scan_qty": new_qty,
                "count_source": "scan",
                "is_counted": True,
                "was_scanned": True,
                "last_scanned_by": self.env.user.id,
                "last_scanned_at": fields.Datetime.now(),
            })
            match_source = "inventory_item"
        else:
            product = self.env["product.product"].search([("barcode", "=", code)], limit=1)
            if not product:
                self._log_scan_event(
                    event_type="missing",
                    match_source="missing",
                    barcode=code,
                    message="Cannot save quantity because barcode was not found",
                )
                raise UserError(self.env._("Barcode not found in Odoo."))
            previous_qty = 0.0
            line = self.env["retailit.stock.inventory.line"].create({
                "inventory_id": self.id,
                "product_id": product.id,
                "location_id": self.location_id.id,
                "lot_id": False,
                "theoretical_qty": self._get_theoretical_qty(product.id),
                "product_qty": new_qty,
                "scan_qty": new_qty,
                "count_source": "scan",
                "is_counted": True,
                "was_scanned": True,
                "last_scanned_by": self.env.user.id,
                "last_scanned_at": fields.Datetime.now(),
            })
            match_source = "product_product"

        self._remove_missed_item(line.product_id.id)
        self._log_scan_event(
            event_type="save",
            match_source=match_source,
            line_id=line.id,
            product_id=line.product_id.id,
            barcode=code,
            previous_qty=previous_qty,
            new_qty=new_qty,
            message="Quantity saved from scanner",
        )
        return self._scan_payload(code, "inventory_item", line.product_id, line)

    def _import_from_excel(self):
        """Import RFID lines by barcode in batches from the first sheet."""
        self.ensure_one()
        file_content = base64.b64decode(self.import_file)
        return self._import_rfid_rows(self._read_rfid_import_rows(file_content))

    def action_import_from_cloud(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(self.env._("Cloud import is only available in draft inventories."))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Import from Beyond ID"),
            "res_model": "retailit.stock.inventory.cloud.import.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_inventory_id": self.id,
            },
        }

    def action_validate(self):
        """Validate inventory and create moves for differences."""
        for inv in self:
            if inv.state != "in_progress":
                raise UserError(self.env._("Only in-progress inventories can be validated."))
            if not inv.analytic_account_id:
                raise UserError(self.env._("An Analytic Account is required before validating the inventory."))

            inv._refresh_missed_items()
            lines_with_diff = inv.line_ids.filtered(lambda l: l.difference_qty != 0)
            if lines_with_diff:
                inv._create_moves(lines_with_diff)
            
            inv.state = "done"

    def _create_moves(self, lines):
        """Create stock moves for differences."""
        self.ensure_one()
        move_vals = []
        
        for line in lines:
            diff = line.difference_qty
            product = line.product_id.with_company(self.company_id)
            inv_location = product.property_stock_inventory
            
            if not inv_location:
                inv_location = self.env["stock.location"].search([
                    ("usage", "=", "inventory"),
                    "|", ("company_id", "=", self.company_id.id),
                    ("company_id", "=", False),
                ], limit=1)
            
            if not inv_location:
                raise UserError(self.env._("Inventory adjustment location not found."))
            
            if diff > 0:
                src, dst = inv_location, line.location_id
            else:
                src, dst = line.location_id, inv_location
                diff = abs(diff)
            
            move_vals.append({
                "retailit_stock_inventory_id": self.id,
                "product_id": line.product_id.id,
                "product_uom_qty": diff,
                "product_uom": line.product_id.uom_id.id,
                "location_id": src.id,
                "location_dest_id": dst.id,
                "company_id": self.company_id.id,
                "is_inventory": True,
                "inventory_name": self.name,
                "state": "confirmed",
                "picked": True,
                "move_line_ids": [(0, 0, {
                    "product_id": line.product_id.id,
                    "product_uom_id": line.product_id.uom_id.id,
                    "quantity": diff,
                    "location_id": src.id,
                    "location_dest_id": dst.id,
                    "lot_id": line.lot_id.id,
                    "package_id": line.package_id.id if src != inv_location else False,
                    "result_package_id": line.package_id.id if dst != inv_location else False,
                    "owner_id": line.owner_id.id,
                    "company_id": self.company_id.id,
                })],
            })
        
        moves = self.env["stock.move"].create(move_vals)
        moves._action_done()

        # Apply the analytic account to every accounting line produced by these moves.
        # analytic_distribution is the Odoo 17+ JSON field: {account_id_str: percentage}.
        if self.analytic_account_id:
            analytic_distribution = {str(self.analytic_account_id.id): 100}
            account_move_lines = moves.mapped("account_move_id.line_ids")
            if account_move_lines:
                account_move_lines.write({"analytic_distribution": analytic_distribution})

    def action_cancel(self):
        """Cancel inventory."""
        for inv in self:
            if inv.state == "done":
                raise UserError(self.env._("Cannot cancel a validated inventory."))
            inv.state = "cancel"

    def action_draft(self):
        """Return to draft."""
        for inv in self:
            if inv.state not in ("cancel", "in_progress"):
                raise UserError(self.env._("Cannot return to draft."))
            inv.line_ids.unlink()
            inv.missing_barcode_ids.unlink()
            inv.missed_item_ids.unlink()
            inv.scan_event_ids.unlink()
            inv.state = "draft"

    def action_view_lines(self):
        """Open lines view."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Inventory Lines"),
            "res_model": "retailit.stock.inventory.line",
            "view_mode": "list",
            "domain": [
                "&",
                ("inventory_id", "=", self.id),
                "|",
                ("count_source", "!=", "system"),
                ("is_counted", "=", True),
            ],
            "context": {"default_inventory_id": self.id},
        }

    def action_view_shortages(self):
        """View shortages."""
        self.ensure_one()
        if self.count_mode == "full":
            self._refresh_missed_items()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Shortages"),
            "res_model": "retailit.stock.inventory.line",
            "view_mode": "list",
            "domain": [("inventory_id", "=", self.id), ("difference_qty", "<", 0)],
        }

    def action_view_surpluses(self):
        """View surpluses."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Surpluses"),
            "res_model": "retailit.stock.inventory.line",
            "view_mode": "list",
            "domain": [("inventory_id", "=", self.id), ("difference_qty", ">", 0)],
        }

    def action_view_missing_barcodes(self):
        """View barcodes imported or scanned but not found in Odoo."""
        self.ensure_one()
        list_view = self.env.ref("retailit_stock_inventory_adjustment.retailit_stock_inventory_missing_barcode_list")
        form_view = self.env.ref("retailit_stock_inventory_adjustment.retailit_stock_inventory_missing_barcode_form")
        search_view = self.env.ref("retailit_stock_inventory_adjustment.retailit_stock_inventory_missing_barcode_search")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Missing Barcodes"),
            "res_model": "retailit.stock.inventory.missing.barcode",
            "view_mode": "list,form",
            "views": [(list_view.id, "list"), (form_view.id, "form")],
            "domain": [("inventory_id", "=", self.id)],
            "context": {"default_inventory_id": self.id},
            "search_view_id": search_view.id,
        }

    def action_view_missed_items(self):
        """View theoretical stock products not counted in a full count."""
        self.ensure_one()
        self._refresh_missed_items()
        list_view = self.env.ref("retailit_stock_inventory_adjustment.retailit_stock_inventory_missed_item_list")
        form_view = self.env.ref("retailit_stock_inventory_adjustment.retailit_stock_inventory_missed_item_form")
        search_view = self.env.ref("retailit_stock_inventory_adjustment.retailit_stock_inventory_missed_item_search")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Missed Items"),
            "res_model": "retailit.stock.inventory.missed.item",
            "view_mode": "list,form",
            "views": [(list_view.id, "list"), (form_view.id, "form")],
            "domain": [("inventory_id", "=", self.id)],
            "context": {"default_inventory_id": self.id},
            "search_view_id": search_view.id,
        }

    def action_refresh_missed_items(self):
        self.ensure_one()
        self._refresh_missed_items()
        return {
            "missed_item_count": len(self.missed_item_ids),
            "missing_barcode_count": len(self.missing_barcode_ids),
            "line_count": len(self.line_ids),
        }

    def write(self, vals):
        if 'state' not in vals and any(r.state in ('done', 'cancel') for r in self):
            raise UserError(self.env._("You cannot modify inventories when they are Validated or Cancelled."))
        inventories_to_refresh = self.browse()
        if "line_ids" in vals:
            inventories_to_refresh = self.filtered(
                lambda inventory: inventory.state == "in_progress" and inventory.count_mode == "full"
            )
        result = super().write(vals)
        if inventories_to_refresh:
            inventories_to_refresh.invalidate_recordset(["line_ids", "missed_item_ids"])
            inventories_to_refresh._refresh_missed_items()
        return result

    def unlink(self):
        for inv in self:
            if inv.state != "draft":
                raise UserError(self.env._("You can only delete inventories in Draft state."))
        return super().unlink()


class StockMove(models.Model):
    _inherit = "stock.move"

    retailit_stock_inventory_id = fields.Many2one(
        "retailit.stock.inventory", "Inventory Adjustment", index=True
    )

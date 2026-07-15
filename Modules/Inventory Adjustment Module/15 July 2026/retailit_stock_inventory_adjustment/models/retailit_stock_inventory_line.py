from odoo import api, fields, models
from odoo.exceptions import UserError


class RetailitStockInventoryLine(models.Model):
    _name = "retailit.stock.inventory.line"
    _description = "Inventory Adjustment Line"
    _order = "was_scanned desc, last_scanned_at desc, id desc"

    _inventory_idx = models.Index("(inventory_id)")
    _inventory_product_idx = models.Index("(inventory_id, product_id)")
    _inventory_counted_idx = models.Index("(inventory_id, is_counted)")
    _inventory_source_counted_idx = models.Index("(inventory_id, count_source, is_counted)")
    _inventory_difference_idx = models.Index("(inventory_id, difference_qty)")

    inventory_id = fields.Many2one(comodel_name="retailit.stock.inventory", string="Inventory", required=True, ondelete="cascade")
    product_id = fields.Many2one(comodel_name="product.product", string="Product", required=True, domain="[('is_storable', '=', True)]")
    location_id = fields.Many2one(comodel_name="stock.location", string="Location", required=True, domain="[('usage', '=', 'internal')]")
    lot_id = fields.Many2one(comodel_name="stock.lot", string="Lot/Serial", domain="[('product_id', '=', product_id)]")
    package_id = fields.Many2one(comodel_name="stock.package", string="Package")
    owner_id = fields.Many2one(comodel_name="res.partner", string="Owner")
    theoretical_qty = fields.Float(string="Theoretical Qty", digits="Product Unit of Measure")
    product_qty = fields.Float(string="Counted Qty", digits="Product Unit of Measure")
    difference_qty = fields.Float(string="Difference", compute="_compute_difference", store=True, digits="Product Unit of Measure")
    count_source = fields.Selection([
        ("rfid", "RFID"),
        ("scan", "Scan"),
        ("manual", "Manual"),
        ("system", "System"),
    ], string="Count Source", default="manual")
    is_counted = fields.Boolean(string="Counted", default=False)
    was_scanned = fields.Boolean(string="Scanned", default=False, index=True)
    scan_qty = fields.Float(string="Last Scan Qty", digits="Product Unit of Measure")
    last_scanned_by = fields.Many2one(comodel_name="res.users", string="Last Scanned By", readonly=True)
    last_scanned_at = fields.Datetime(string="Last Scanned At", readonly=True)
    state = fields.Selection(related="inventory_id.state", store=True)
    company_id = fields.Many2one(related="inventory_id.company_id", store=True)
    categ_id = fields.Many2one(comodel_name="product.category", related="product_id.categ_id", string="Category", store=True)

    @api.depends("theoretical_qty", "product_qty")
    def _compute_difference(self):
        for line in self:
            line.difference_qty = line.product_qty - line.theoretical_qty

    def _is_system_missed_line(self):
        self.ensure_one()
        return self.count_source == "system" and not self.is_counted

    @api.onchange("product_id", "location_id", "lot_id", "package_id", "owner_id")
    def _onchange_product(self):
        """Update theoretical quantity when changing product/location."""
        if not self.product_id or not self.location_id:
            self.theoretical_qty = 0
            return
        
        domain = [
            ("product_id", "=", self.product_id.id),
            ("location_id", "=", self.location_id.id),
        ]
        if self.lot_id:
            domain.append(("lot_id", "=", self.lot_id.id))
        else:
            domain.append(("lot_id", "=", False))
        if self.package_id:
            domain.append(("package_id", "=", self.package_id.id))
        else:
            domain.append(("package_id", "=", False))
        if self.owner_id:
            domain.append(("owner_id", "=", self.owner_id.id))
        else:
            domain.append(("owner_id", "=", False))
        
        quant = self.env["stock.quant"].search(domain, limit=1)
        self.theoretical_qty = quant.quantity if quant else 0.0

    def action_refresh(self):
        """Refresh theoretical quantity."""
        for line in self:
            line._onchange_product()

    def action_reset_qty(self):
        """Reset counted quantity to theoretical."""
        for line in self:
            line.product_qty = line.theoretical_qty

    def write(self, vals):
        if 'state' not in vals and any(r.state in ('done', 'cancel') for r in self):
            raise UserError(self.env._("You cannot modify lines when the inventory is Validated or Cancelled."))
        mark_manual_count = (
            "product_qty" in vals
            and not self.env.context.get("skip_manual_count_on_qty_write")
            and any(line.state == "in_progress" for line in self)
        )
        if mark_manual_count:
            vals = dict(vals)
            vals.setdefault("is_counted", True)
            vals.setdefault("count_source", "manual")

        result = super().write(vals)

        if mark_manual_count:
            inventories = self.inventory_id.filtered(
                lambda inventory: inventory.state == "in_progress" and inventory.count_mode == "full"
            )
            for line in self.filtered(lambda line: line.inventory_id in inventories and line.product_id and line.is_counted):
                line.inventory_id._remove_missed_item(line.product_id.id)
            if inventories:
                inventories.invalidate_recordset(["line_ids", "missed_item_ids"])
        return result

    def _get_deleted_line_missed_candidates(self):
        candidates = {}
        for line in self:
            inventory = line.inventory_id
            if inventory.state != "in_progress" or inventory.count_mode != "full" or not line.product_id:
                continue
            key = (inventory.id, line.product_id.id)
            theoretical_qty = line.theoretical_qty or inventory._get_theoretical_qty(line.product_id.id)
            if theoretical_qty == 0:
                continue
            if key not in candidates or theoretical_qty > candidates[key]["theoretical_qty"]:
                candidates[key] = {
                    "inventory_id": inventory.id,
                    "product_id": line.product_id.id,
                    "barcode": line.product_id.barcode,
                    "location_id": inventory.location_id.id,
                    "theoretical_qty": theoretical_qty,
                }
        return candidates

    def unlink(self):
        if self.env.context.get("skip_missed_refresh"):
            return super().unlink()
        missed_candidates = self._get_deleted_line_missed_candidates()
        inventories_to_refresh = self.inventory_id.filtered(
            lambda inventory: inventory.state == "in_progress" and inventory.count_mode == "full"
        )
        result = super().unlink()
        if inventories_to_refresh:
            inventories_to_refresh.invalidate_recordset(["line_ids", "missed_item_ids"])
            inventories_to_refresh._refresh_missed_items()
            MissedItem = self.env["retailit.stock.inventory.missed.item"]
            InventoryLine = self.env["retailit.stock.inventory.line"]
            for (inventory_id, product_id), vals in missed_candidates.items():
                if InventoryLine.search_count([
                    ("inventory_id", "=", inventory_id),
                    ("product_id", "=", product_id),
                    ("is_counted", "=", True),
                ]):
                    continue
                if MissedItem.search_count([
                    ("inventory_id", "=", inventory_id),
                    ("product_id", "=", product_id),
                    ("location_id", "=", vals["location_id"]),
                ]):
                    continue
                MissedItem.create(vals)
            inventories_to_refresh.invalidate_recordset(["line_ids", "missed_item_ids"])
        return result

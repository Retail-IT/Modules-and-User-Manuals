# -*- coding: utf-8 -*-
from odoo import fields, models

STATE_LABELS = {
    "draft": "Draft",
    "waiting": "Waiting",
    "confirmed": "Waiting",
    "assigned": "Ready",
}


class PosSession(models.Model):
    _inherit = "pos.session"

    # ------------------------------------------------------------------
    # Configuration payload for the POS frontend
    # ------------------------------------------------------------------

    def get_transfer_config(self):
        """Return this POS's stock transfer configuration for the frontend.

        Destinations come from the global retailit.pos.transfer.location
        list (Point of Sale > Configuration > Transfer Locations): every
        other location on that list is a valid destination for this POS.
        """
        self.ensure_one()
        my_location = self.config_id.transfer_location_id
        if not my_location:
            return {
                "enabled": False,
                "source_location_id": False,
                "source_location_name": "",
                "destinations": [],
            }

        other_locations = self.env["retailit.pos.transfer.location"].sudo().search(
            [("id", "!=", my_location.id)]
        )
        destinations = [
            {
                "location_id": loc.location_id.id,
                "location_name": loc.location_id.display_name,
                "picking_type_id": loc.picking_type_id.id,
            }
            for loc in other_locations
        ]

        return {
            "enabled": True,
            "source_location_id": my_location.location_id.id,
            "source_location_name": my_location.location_id.display_name,
            "destinations": destinations,
        }

    # ------------------------------------------------------------------
    # Sending a transfer
    # ------------------------------------------------------------------

    def check_transfer_products(self, product_lines):
        """Split requested product lines into stockable and service products.

        Returns (stockable_product_ids, service_product_ids). Service
        products cannot be moved by stock.move and are reported back so the
        cashier can be warned that they will be skipped.
        """
        product_obj = self.env["product.product"]
        stockable_ids = []
        service_ids = []
        for line in product_lines or []:
            product = product_obj.browse(int(line.get("product_id") or 0)).exists()
            if not product:
                continue
            if product.type in ("consu", "product"):
                stockable_ids.append(product.id)
            else:
                service_ids.append(product.id)
        return stockable_ids, service_ids

    def create_transfer(self, partner_id, picking_type_id, source_location_id,
                         dest_location_id, product_lines, staff_member=None):
        """Create and confirm an internal transfer for the given product lines."""
        self.ensure_one()
        picking_type = self.env["stock.picking.type"].browse(int(picking_type_id)).exists()
        source_location = self.env["stock.location"].browse(int(source_location_id)).exists()
        dest_location = self.env["stock.location"].browse(int(dest_location_id)).exists()
        if not (picking_type and source_location and dest_location):
            return {"success": False, "message": "Invalid transfer configuration."}

        vals = {
            "company_id": self.config_id.company_id.id,
            "partner_id": partner_id or False,
            "location_id": source_location.id,
            "location_dest_id": dest_location.id,
            "picking_type_id": picking_type.id,
        }
        staff_member = (staff_member or "").strip()
        if staff_member:
            vals["transfer_requested_by"] = staff_member

        picking = self.env["stock.picking"].create(vals)
        self._create_transfer_moves(picking, product_lines)
        picking.action_confirm()
        try:
            picking.action_assign()
        except Exception:
            pass

        return {"success": True, "picking_id": picking.id, "picking_name": picking.name}

    def _create_transfer_moves(self, picking, product_lines):
        product_obj = self.env["product.product"]
        move_obj = self.env["stock.move"]
        for line in product_lines or []:
            product = product_obj.browse(int(line.get("product_id") or 0)).exists()
            qty = float(line.get("quantity") or 0)
            if not product or qty <= 0 or product.type not in ("consu", "product"):
                continue
            move_obj.create({
                "product_id": product.id,
                "product_uom_qty": qty,
                "picking_id": picking.id,
                "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id,
                "product_uom": product.uom_id.id,
                "picking_type_id": picking.picking_type_id.id,
            })

    # ------------------------------------------------------------------
    # Open transfers sent from this POS
    # ------------------------------------------------------------------

    def get_open_transfers(self):
        self.ensure_one()
        config = self.config_id
        source_location = config.transfer_location_id.location_id
        if not source_location:
            return {"transfers": [], "warning": "No transfer location is configured for this Point of Sale."}

        domain = [
            ("location_id", "=", source_location.id),
            ("picking_type_id.code", "=", "internal"),
            ("state", "in", ["draft", "waiting", "confirmed", "assigned"]),
        ]
        if config.company_id:
            domain.append(("company_id", "=", config.company_id.id))

        pickings = self.env["stock.picking"].sudo().search(domain, order="create_date desc, id desc", limit=50)
        return {"transfers": [self._prepare_transfer_payload(p) for p in pickings], "warning": False}

    def _assert_transfer_editable(self, picking):
        config = self.config_id
        if not picking:
            return False, "Transfer not found."
        if picking.state in ("done", "cancel"):
            return False, "This transfer is already closed."
        if picking.picking_type_id.code != "internal":
            return False, "This is not an internal transfer."
        source_location = config.transfer_location_id.location_id
        if source_location and picking.location_id.id != source_location.id:
            return False, "This transfer was not sent from this Point of Sale."
        return True, False

    def add_products_to_open_transfer(self, picking_id, product_lines):
        """Append current POS cart products to an open (not yet validated) transfer."""
        self.ensure_one()
        picking = self.env["stock.picking"].sudo().browse(int(picking_id)).exists()
        ok, message = self._assert_transfer_editable(picking)
        if not ok:
            return {"success": False, "message": message}

        added_qty = 0
        move_obj = self.env["stock.move"].sudo()
        product_obj = self.env["product.product"].sudo()
        for line in product_lines or []:
            product = product_obj.browse(int(line.get("product_id") or 0)).exists()
            qty = float(line.get("quantity") or 0)
            if not product or qty <= 0 or product.type not in ("consu", "product"):
                continue

            existing_move = picking.move_ids.filtered(
                lambda m, product=product: m.product_id.id == product.id and m.state not in ("done", "cancel")
            )[:1]
            if existing_move:
                existing_move.product_uom_qty += qty
            else:
                move_obj.create({
                    "product_id": product.id,
                    "product_uom_qty": qty,
                    "picking_id": picking.id,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "product_uom": product.uom_id.id,
                    "picking_type_id": picking.picking_type_id.id,
                })
            added_qty += qty

        if added_qty <= 0:
            return {"success": False, "message": "No stockable or consumable products were added."}

        try:
            picking.action_assign()
        except Exception:
            pass

        return {
            "success": True,
            "message": "Open transfer updated successfully.",
            "transfer": self._prepare_transfer_payload(picking),
        }

    def cancel_open_transfer(self, picking_id):
        self.ensure_one()
        picking = self.env["stock.picking"].sudo().browse(int(picking_id)).exists()
        ok, message = self._assert_transfer_editable(picking)
        if not ok:
            return {"success": False, "message": message}
        picking.action_cancel()
        return {"success": True, "message": "Open transfer cancelled.", "name": picking.name}

    # ------------------------------------------------------------------
    # Receiving transfers and purchase receipts
    # ------------------------------------------------------------------

    def get_incoming_transfers(self):
        self.ensure_one()
        config = self.config_id
        picking_type = config.transfer_location_id.picking_type_id
        if not picking_type:
            return {"transfers": [], "warning": "No transfer location is configured for this Point of Sale."}

        domain = [
            ("picking_type_id", "=", picking_type.id),
            ("state", "in", ["waiting", "confirmed", "assigned"]),
        ]
        if config.company_id:
            domain.append(("company_id", "=", config.company_id.id))

        pickings = self.env["stock.picking"].sudo().search(domain, order="scheduled_date asc, id asc", limit=50)
        return {"transfers": [self._prepare_transfer_payload(p) for p in pickings], "warning": False}

    def _validate_picking_from_pos(self, picking, employee_id=None):
        """Shared validation routine used for both transfers and purchase receipts."""
        if picking.state == "draft":
            picking.action_confirm()

        try:
            picking.action_assign()
        except Exception:
            pass

        move_line_obj = self.env["stock.move.line"].sudo()
        for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel")):
            qty_to_receive = move.product_uom_qty
            if qty_to_receive <= 0:
                continue

            move_lines = move.move_line_ids
            if move_lines:
                remaining = qty_to_receive
                for line in move_lines:
                    line.quantity = remaining if remaining > 0 else 0
                    remaining = 0
            else:
                move_line_obj.create({
                    "picking_id": picking.id,
                    "move_id": move.id,
                    "product_id": move.product_id.id,
                    "product_uom_id": move.product_uom.id,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "quantity": qty_to_receive,
                })

        result = picking.button_validate()

        if isinstance(result, dict):
            res_model = result.get("res_model")
            context = result.get("context") or {}
            if res_model == "stock.immediate.transfer":
                self.env[res_model].sudo().with_context(context).create({}).process()
            elif res_model == "stock.backorder.confirmation":
                self.env[res_model].sudo().with_context(context).create({}).process_cancel_backorder()

        if picking.state == "done" and employee_id:
            try:
                picking.sudo().write({"transfer_validated_by": int(employee_id)})
            except Exception:
                pass

        picking.invalidate_recordset()
        return picking.state == "done"

    def validate_incoming_transfer(self, picking_id, employee_id=None):
        self.ensure_one()
        config = self.config_id
        picking = self.env["stock.picking"].sudo().browse(int(picking_id)).exists()
        if not picking:
            return {"success": False, "message": "Transfer not found."}
        if picking.state in ("done", "cancel"):
            return {"success": False, "message": "This transfer is already closed."}
        expected_picking_type = config.transfer_location_id.picking_type_id
        if expected_picking_type and picking.picking_type_id.id != expected_picking_type.id:
            return {"success": False, "message": "This transfer is not destined for this Point of Sale."}

        done = self._validate_picking_from_pos(picking, employee_id)
        return {
            "success": done,
            "message": "Transfer received successfully." if done else "Transfer could not be fully validated. Please check it in the backend.",
            "name": picking.name,
            "state": picking.state,
        }

    def get_incoming_purchase_receipts(self):
        self.ensure_one()
        config = self.config_id
        receiving_location = config.transfer_location_id.location_id
        if not receiving_location:
            return {"receipts": [], "warning": "No transfer location is configured for this Point of Sale."}

        domain = [
            ("location_dest_id", "=", receiving_location.id),
            ("picking_type_id.code", "=", "incoming"),
            ("state", "in", ["waiting", "confirmed", "assigned"]),
        ]
        if config.company_id:
            domain.append(("company_id", "=", config.company_id.id))

        pickings = self.env["stock.picking"].sudo().search(domain, order="scheduled_date asc, id asc", limit=50)
        return {"receipts": [self._prepare_transfer_payload(p) for p in pickings], "warning": False}

    def validate_purchase_receipt(self, picking_id, employee_id=None):
        self.ensure_one()
        config = self.config_id
        picking = self.env["stock.picking"].sudo().browse(int(picking_id)).exists()
        if not picking:
            return {"success": False, "message": "Receipt not found."}
        if picking.state in ("done", "cancel"):
            return {"success": False, "message": "This receipt is already closed."}
        if picking.picking_type_id.code != "incoming":
            return {"success": False, "message": "This is not a purchase receipt."}

        receiving_location = config.transfer_location_id.location_id
        if receiving_location and picking.location_dest_id.id != receiving_location.id:
            return {"success": False, "message": "This receipt is not destined for this Point of Sale."}

        done = self._validate_picking_from_pos(picking, employee_id)
        return {
            "success": done,
            "message": "Receipt validated successfully." if done else "Receipt could not be fully validated. Please check it in the backend.",
            "name": picking.name,
            "state": picking.state,
        }

    # ------------------------------------------------------------------
    # Employee PIN check
    # ------------------------------------------------------------------

    def verify_employee_pin(self, pin):
        """Verify an employee PIN server-side and return the matching employee.

        PINs are verified server-side so they are never exposed to the
        client. This is the standard Odoo HR employee PIN used for POS login.
        """
        self.ensure_one()
        if not pin:
            return {"success": False, "message": "No PIN entered."}

        employee = self.env["hr.employee"].sudo().search([
            ("pin", "=", str(pin).strip()),
            ("active", "=", True),
            ("company_id", "=", self.config_id.company_id.id),
        ], limit=1)

        if not employee:
            return {"success": False, "message": "PIN not recognised. Please try again."}

        return {"success": True, "employee_id": employee.id, "name": employee.name}

    # ------------------------------------------------------------------
    # Shared payload builder
    # ------------------------------------------------------------------

    def _prepare_transfer_payload(self, picking):
        items = []
        for move in picking.move_ids:
            if move.state == "cancel":
                continue
            items.append({
                "move_id": move.id,
                "product_id": move.product_id.id,
                "display_name": move.product_id.display_name,
                "quantity": move.product_uom_qty,
                "uom": move.product_uom.name,
            })

        picking_date = picking.date_done or picking.scheduled_date or picking.create_date
        formatted_date = fields.Datetime.to_string(picking_date) if picking_date else False

        return {
            "id": picking.id,
            "name": picking.name,
            "state": picking.state,
            "state_label": STATE_LABELS.get(picking.state, picking.state),
            "source": picking.location_id.display_name,
            "destination": picking.location_dest_id.display_name,
            "operation_type": picking.picking_type_id.display_name,
            "item_count": len(items),
            "total_qty": sum(item["quantity"] for item in items),
            "items": items,
            "date": formatted_date,
            "staff_requesting": picking.transfer_requested_by or "",
        }

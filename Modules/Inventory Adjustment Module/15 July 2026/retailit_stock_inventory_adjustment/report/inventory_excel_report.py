# -*- coding: utf-8 -*-
import io
import re
import base64
from odoo import models

try:
    import xlsxwriter
    XLSXWRITER_AVAILABLE = True
except ImportError:
    XLSXWRITER_AVAILABLE = False


class RetailitStockInventoryExcel(models.Model):
    _inherit = 'retailit.stock.inventory'

    def action_export_excel(self):
        """Generate Excel report with same structure as PDF."""
        self.ensure_one()
        _ = self.env._
        
        if not XLSXWRITER_AVAILABLE:
            from odoo.exceptions import UserError
            raise UserError(_("xlsxwriter library is not installed. Please install it with: pip install xlsxwriter"))
        
        # Create Excel file in memory
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        
        inventory = self
        sheet = workbook.add_worksheet(inventory.name[:31])
        
        # Define formats
        title_format = workbook.add_format({
            'bold': True, 'font_size': 16, 'align': 'center', 
            'valign': 'vcenter', 'bg_color': '#343a40', 'font_color': 'white'
        })
        header_format = workbook.add_format({
            'bold': True, 'font_size': 11, 'align': 'center',
            'valign': 'vcenter', 'bg_color': '#e9ecef', 'border': 1
        })
        location_format = workbook.add_format({
            'bold': True, 'font_size': 11, 'bg_color': '#343a40', 
            'font_color': 'white', 'border': 1
        })
        product_format = workbook.add_format({
            'bold': True, 'bg_color': '#f8f9fa', 'border': 1, 'valign': 'vcenter'
        })
        cell_format = workbook.add_format({'border': 1, 'align': 'center'})
        number_format = workbook.add_format({'border': 1, 'align': 'right', 'num_format': '#,##0.00'})
        shortage_format = workbook.add_format({
            'border': 1, 'align': 'right', 'font_color': '#dc3545', 
            'bold': True, 'num_format': '#,##0.00'
        })
        surplus_format = workbook.add_format({
            'border': 1, 'align': 'right', 'font_color': '#28a745', 
            'bold': True, 'num_format': '#,##0.00'
        })
        shortage_title_format = workbook.add_format({
            'bold': True, 'font_size': 14, 'bg_color': '#dc3545', 'font_color': 'white'
        })
        surplus_title_format = workbook.add_format({
            'bold': True, 'font_size': 14, 'bg_color': '#28a745', 'font_color': 'white'
        })
        
        # Set column widths
        sheet.set_column('A:A', 35)  # Product
        sheet.set_column('B:B', 20)  # Location
        sheet.set_column('C:C', 18)  # Lot/Serial
        sheet.set_column('D:F', 12)  # Theoretical / Counted / Difference
        
        row = 0
        
        # Title
        sheet.merge_range(row, 0, row, 5, _('Inventory Adjustment: %s') % inventory.name, title_format)
        row += 2
        
        # Info
        sheet.write(row, 0, _('Location:'), header_format)
        sheet.write(row, 1, inventory.location_id.display_name or '', cell_format)
        sheet.write(row, 2, _('Date:'), header_format)
        sheet.merge_range(row, 3, row, 5, str(inventory.date.date()) if inventory.date else '', cell_format)
        row += 1
        sheet.write(row, 0, _('Type:'), header_format)
        sheet.write(row, 1, dict(inventory._fields['inventory_type'].selection).get(inventory.inventory_type, ''), cell_format)
        sheet.write(row, 2, _('State:'), header_format)
        sheet.merge_range(row, 3, row, 5, dict(inventory._fields['state'].selection).get(inventory.state, ''), cell_format)
        row += 2
        
        # All Lines Section
        sheet.merge_range(row, 0, row, 5, _('All Lines'), title_format)
        row += 1
        
        # Table header
        headers = [_('Product'), _('Location'), _('Lot/Serial'), _('Theoretical'), _('Counted'), _('Difference')]
        for col, header in enumerate(headers):
            sheet.write(row, col, header, header_format)
        row += 1
        
        # Lines grouped by location
        lines_by_location = {}
        for line in inventory.line_ids:
            loc_name = line.location_id.display_name
            if loc_name not in lines_by_location:
                lines_by_location[loc_name] = []
            lines_by_location[loc_name].append(line)
        
        for loc_name, lines in lines_by_location.items():
            # Location header
            total_theo = sum(l.theoretical_qty for l in lines)
            total_count = sum(l.product_qty for l in lines)
            total_diff = sum(l.difference_qty for l in lines)
            
            sheet.merge_range(row, 0, row, 5, 
                f"{loc_name} | {_('Theoretical')}: {total_theo:.2f} | {_('Counted')}: {total_count:.2f} | {_('Diff')}: {total_diff:.2f}",
                location_format)
            row += 1
            
            for line in lines:
                sheet.write(row, 0, line.product_id.display_name, product_format)
                sheet.write(row, 1, line.location_id.display_name, cell_format)
                sheet.write(row, 2, line.lot_id.display_name if line.lot_id else '', cell_format)
                sheet.write(row, 3, line.theoretical_qty, number_format)
                sheet.write(row, 4, line.product_qty, number_format)
                
                diff = line.difference_qty
                diff_fmt = shortage_format if diff < 0 else (surplus_format if diff > 0 else number_format)
                sheet.write(row, 5, diff, diff_fmt)
                row += 1
            
            row += 1
        
        # Shortages Section
        shortage_lines = inventory.line_ids.filtered(lambda l: l.difference_qty < 0)
        if shortage_lines:
            row += 1
            sheet.merge_range(row, 0, row, 5, _('Shortages (Missing Stock)'), shortage_title_format)
            row += 1
            
            headers = [_('Product'), _('Location'), _('Lot/Serial'), _('Theoretical'), _('Counted'), _('Shortage')]
            for col, header in enumerate(headers):
                sheet.write(row, col, header, header_format)
            row += 1
            
            for line in shortage_lines:
                sheet.write(row, 0, line.product_id.display_name, product_format)
                sheet.write(row, 1, line.location_id.display_name, cell_format)
                sheet.write(row, 2, line.lot_id.display_name if line.lot_id else '', cell_format)
                sheet.write(row, 3, line.theoretical_qty, number_format)
                sheet.write(row, 4, line.product_qty, number_format)
                sheet.write(row, 5, line.difference_qty, shortage_format)
                row += 1
        
        # Surpluses Section
        surplus_lines = inventory.line_ids.filtered(lambda l: l.difference_qty > 0)
        if surplus_lines:
            row += 1
            sheet.merge_range(row, 0, row, 5, _('Surpluses (Extra Stock)'), surplus_title_format)
            row += 1
            
            headers = [_('Product'), _('Location'), _('Lot/Serial'), _('Theoretical'), _('Counted'), _('Surplus')]
            for col, header in enumerate(headers):
                sheet.write(row, col, header, header_format)
            row += 1
            
            for line in surplus_lines:
                sheet.write(row, 0, line.product_id.display_name, product_format)
                sheet.write(row, 1, line.location_id.display_name, cell_format)
                sheet.write(row, 2, line.lot_id.display_name if line.lot_id else '', cell_format)
                sheet.write(row, 3, line.theoretical_qty, number_format)
                sheet.write(row, 4, line.product_qty, number_format)
                sheet.write(row, 5, line.difference_qty, surplus_format)
                row += 1
        
        # Summary
        row += 2
        sheet.merge_range(row, 0, row, 5, _('Summary'), title_format)
        row += 1
        sheet.write(row, 0, _('Total Lines:'), header_format)
        sheet.write(row, 1, len(inventory.line_ids), number_format)
        sheet.write(row, 2, _('Shortages:'), header_format)
        sheet.write(row, 3, len(shortage_lines), number_format)
        row += 1
        sheet.write(row, 0, _('Surpluses:'), header_format)
        sheet.write(row, 1, len(surplus_lines), number_format)

        # =====================================================
        # Variance Report Sheet
        # =====================================================
        variance_sheet = workbook.add_worksheet('Variance Report')

        variance_sheet.set_column('A:A', 50)
        variance_sheet.set_column('B:B', 25)
        variance_sheet.set_column('C:F', 15)

        variance_sheet.freeze_panes(1, 0)

        variance_headers = [
            _('Product'),
            _('Location'),
            _('Theoretical'),
            _('Counted'),
            _('Variance'),
            _('Recheck Qty')
        ]

        for col, header in enumerate(variance_headers):
            variance_sheet.write(0, col, header, header_format)

        variance_lines = inventory.line_ids.filtered(
            lambda l: l.difference_qty != 0
        )

        sorted_variance_lines = sorted(
            variance_lines,
            key=lambda l: re.sub(
                r'\[.*?\]\s*',
                '',
                l.product_id.display_name or ''
            ).strip().lower()
        )

        variance_row = 1

        for line in sorted_variance_lines:
            clean_name = re.sub(
                r'\[.*?\]\s*',
                '',
                line.product_id.display_name or ''
            ).strip()

            variance_sheet.write(
                variance_row,
                0,
                clean_name,
                product_format
            )

            variance_sheet.write(
                variance_row,
                1,
                line.location_id.display_name,
                cell_format
            )

            variance_sheet.write(
                variance_row,
                2,
                line.theoretical_qty,
                number_format
            )

            variance_sheet.write(
                variance_row,
                3,
                line.product_qty,
                number_format
            )

            diff = line.difference_qty
            diff_fmt = shortage_format if diff < 0 else surplus_format

            variance_sheet.write(
                variance_row,
                4,
                diff,
                diff_fmt
            )

            variance_sheet.write(
                variance_row,
                5,
                '',
                cell_format
            )

            variance_row += 1

        if variance_row > 1:
            variance_sheet.autofilter(
                0,
                0,
                variance_row - 1,
                len(variance_headers) - 1
            )

        workbook.close()
        output.seek(0)
        
        # Create attachment
        filename = f"inventory_{inventory.name}_{inventory.date.date() if inventory.date else 'no_date'}.xlsx"
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(output.read()),
            'res_model': 'retailit.stock.inventory',
            'res_id': inventory.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

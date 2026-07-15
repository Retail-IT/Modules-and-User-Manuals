/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { InvoiceExtractFormRenderer } from "@account_invoice_extract/js/invoice_extract_form";

/**
 * Fixes a crash in Odoo core's OCR "Digitize documents" feature:
 * ExtractMixinFormRenderer.getBoxType() (iap_extract) builds a
 * "parentField.fieldName" name whenever the focused element sits inside
 * ANY ancestor carrying the .o_field_widget class, assuming that ancestor
 * is always an x2many sub-record (e.g. invoice_line_ids) with a real
 * `._config.fields` structure. The native analytic_distribution widget
 * renders its own internal nested .o_field_widget-classed markup (the
 * account picker), so focusing it on a plain (non-x2many) field such as
 * this module's account.move-level analytic_distribution triggers the
 * same dotted-name code path with no real x2many parent behind it -
 * `record.data[parentField]` is just a plain JSON object with no
 * `._config`, and the original code reads `._config.fields` without an
 * optional chain, throwing "Cannot read properties of undefined
 * (reading 'fields')".
 *
 * That listener is registered on `window` (not scoped to a single form
 * instance), so it fires for any focus event anywhere on the page while
 * an account.move form using this renderer is mounted underneath -
 * including inside dialogs/wizards opened from that form. Moving the
 * field into a wizard does not avoid this; only fixing the underlying
 * method does.
 *
 * This patch only intercepts the specific crash-prone case (a dotted
 * field name whose parent isn't a real x2many sub-record) and safely
 * returns false - no OCR box type for it, no crash. Every other case
 * (genuine x2many sub-fields, e.g. line-level analytic_distribution
 * inside invoice_line_ids) is left untouched, delegating to the
 * original implementation via super().
 */
patch(InvoiceExtractFormRenderer.prototype, {
    getBoxType(fullFieldName) {
        if (fullFieldName && fullFieldName.includes(".")) {
            const [parentField] = fullFieldName.split(".");
            const parentRecord = this.props.record.data[parentField];
            if (!parentRecord || !parentRecord._config) {
                return false;
            }
        }
        return super.getBoxType(fullFieldName);
    },
});

import { _t } from "@web/core/l10n/translation";
import { BarcodeScanner } from "@barcodes/components/barcode_scanner";
import { Component, onMounted, useExternalListener, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class InventoryScanner extends Component {
    static template = "retailit_stock_inventory_adjustment.InventoryScanner";
    static components = { BarcodeScanner };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.barcode = useService("barcode");
        this.inventoryId = this.props.action.context.active_id;
        this.inventoryName = this.props.action.context.inventory_name;
        this.locationName = this.props.action.context.location_name;
        this.initialBarcode = (this.props.action.context.default_barcode || "").trim();
        this.initialResult = this.props.action.context.default_scan_result || null;
        this.continuousScan = Boolean(this.props.action.context.continuous_scan);
        this.quickQtyAdjustments = [
            { label: "+1", value: 1 },
            { label: "+5", value: 5 },
            { label: "+10", value: 10 },
            { label: "-1", value: -1 },
            { label: "-5", value: -5 },
            { label: "-10", value: -10 },
        ];
        this.numpadButtons = [
            { label: "7", value: "7", type: "digit" },
            { label: "8", value: "8", type: "digit" },
            { label: "9", value: "9", type: "digit" },
            { label: "4", value: "4", type: "digit" },
            { label: "5", value: "5", type: "digit" },
            { label: "6", value: "6", type: "digit" },
            { label: "1", value: "1", type: "digit" },
            { label: "2", value: "2", type: "digit" },
            { label: "3", value: "3", type: "digit" },
            { label: "C", value: "clear", type: "clear" },
            { label: "0", value: "0", type: "digit" },
            { label: "Del", value: "backspace", type: "backspace" },
        ];
        this.barcodeInput = useRef("barcodeInput");
        this.qtyNumpadStarted = false;
        this.lastScanWarningAt = 0;
        this.state = useState({
            barcode: this.initialBarcode,
            qty: this.initialResult?.can_save ? String(this.initialResult.current_qty || 0) : "",
            result: this.initialResult,
            loading: false,
            saving: false,
        });
        useBus(this.barcode.bus, "barcode_scanned", (ev) => this.onBarcodeScanned(ev.detail.barcode));
        useExternalListener(window, "keydown", (ev) => this.onGlobalKeydown(ev));
        onMounted(async () => {
            if (this.initialResult) {
                this.qtyNumpadStarted = false;
                if (!this.initialResult.can_save) {
                    this.env.bus.trigger("retailit_stock_inventory_adjustment:scan_recorded", {
                        inventoryId: this.inventoryId,
                        barcode: this.initialResult.barcode,
                        matchSource: this.initialResult.match_source,
                    });
                    this.notification.add(this.initialResult.message || _t("Barcode not found."), {
                        type: "warning",
                    });
                }
            } else if (this.initialBarcode) {
                await this.lookup();
            } else {
                this.focusBarcode();
            }
        });
    }

    isPlausibleBarcode(barcode) {
        return barcode.length <= 64 && !/\s/.test(barcode);
    }

    notifyScanWarning(message) {
        const now = Date.now();
        if (now - this.lastScanWarningAt < 4000) {
            return;
        }
        this.lastScanWarningAt = now;
        this.notification.add(message, { type: "warning" });
    }

    async onGlobalKeydown(ev) {
        if (ev.defaultPrevented || ev.key !== "Enter" || !this.state.result?.can_save || this.state.saving) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        await this.saveQty();
    }

    focusBarcode() {
        this.barcodeInput.el?.focus();
        this.barcodeInput.el?.select();
    }

    get sourceLabel() {
        const source = this.state.result?.match_source;
        if (source === "inventory_item") {
            return _t("Found in Inventory Items");
        }
        if (source === "product_product") {
            return _t("Found in Product Master");
        }
        return _t("Barcode Not Found");
    }

    get sourceClass() {
        const source = this.state.result?.match_source;
        if (source === "inventory_item") {
            return "o_qt_scan_status_item";
        }
        if (source === "product_product") {
            return "o_qt_scan_status_master";
        }
        return "o_qt_scan_status_missing";
    }

    async onBarcodeScanned(barcode) {
        const cleanBarcode = (barcode || "").trim();
        if (!cleanBarcode) {
            return;
        }
        if (!this.isPlausibleBarcode(cleanBarcode)) {
            return;
        }
        if (this.state.result?.can_save) {
            if (cleanBarcode === this.state.result.barcode) {
                this.adjustQty(1);
                return;
            }
            this.notifyScanWarning(_t("Save or cancel the current product before scanning another barcode."));
            return;
        }
        this.state.barcode = cleanBarcode;
        await this.lookup();
    }

    async onBarcodeKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            await this.lookup();
        }
    }

    async onQtyKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            await this.saveQty();
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            this.clearResult();
        }
    }

    getCurrentQty() {
        const qty = Number(this.state.qty || 0);
        return Number.isFinite(qty) ? qty : 0;
    }

    formatQty(qty) {
        return Number.isInteger(qty) ? String(qty) : String(Number(qty.toFixed(3)));
    }

    adjustQty(delta) {
        if (!this.state.result?.can_save || this.state.saving) {
            return;
        }
        const nextQty = Math.max(0, this.getCurrentQty() + delta);
        this.state.qty = this.formatQty(nextQty);
        this.qtyNumpadStarted = true;
    }

    appendQtyDigit(digit) {
        if (!this.state.result?.can_save || this.state.saving) {
            return;
        }
        const current = this.qtyNumpadStarted ? String(this.state.qty || "0") : "0";
        this.state.qty = current === "0" ? digit : `${current}${digit}`;
        this.qtyNumpadStarted = true;
    }

    clearQty() {
        if (!this.state.result?.can_save || this.state.saving) {
            return;
        }
        this.state.qty = "0";
        this.qtyNumpadStarted = true;
    }

    backspaceQty() {
        if (!this.state.result?.can_save || this.state.saving) {
            return;
        }
        const current = String(this.state.qty || "0");
        this.state.qty = current.length > 1 ? current.slice(0, -1) : "0";
        this.qtyNumpadStarted = true;
    }

    onNumpadButton(button) {
        if (button.type === "digit") {
            this.appendQtyDigit(button.value);
        } else if (button.type === "clear") {
            this.clearQty();
        } else if (button.type === "backspace") {
            this.backspaceQty();
        }
    }

    async lookup() {
        const code = this.state.barcode.trim();
        if (!code || this.state.loading) {
            return;
        }
        this.state.loading = true;
        try {
            const result = await this.orm.call("retailit.stock.inventory", "action_scan_barcode", [[this.inventoryId]], {
                barcode: code,
            });
            this.state.result = result;
            this.state.qty = result.can_save ? String(result.current_qty || 0) : "";
            this.qtyNumpadStarted = false;
            if (!result.can_save) {
                this.env.bus.trigger("retailit_stock_inventory_adjustment:scan_recorded", {
                    inventoryId: this.inventoryId,
                    barcode: result.barcode,
                    matchSource: result.match_source,
                });
                this.notification.add(result.message || _t("Barcode not found."), { type: "warning" });
            }
        } catch (error) {
            this.notification.add(error.data?.message || error.message || _t("Scan failed."), {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    async saveQty() {
        if (!this.state.result?.can_save || this.state.saving) {
            return;
        }
        const qty = Number(this.state.qty);
        if (!Number.isFinite(qty)) {
            this.notification.add(_t("Enter a valid quantity."), { type: "danger" });
            return;
        }
        if (qty < 0) {
            this.notification.add(_t("Quantity cannot be negative."), { type: "danger" });
            this.state.qty = "0";
            return;
        }
        this.state.saving = true;
        try {
            const result = await this.orm.call("retailit.stock.inventory", "action_apply_scan_qty", [[this.inventoryId]], {
                barcode: this.state.result.barcode,
                qty,
            });
            this.env.bus.trigger("retailit_stock_inventory_adjustment:scan_saved", {
                inventoryId: this.inventoryId,
                lineId: result.line_id,
            });
            this.notification.add(_t("Quantity saved."), { type: "success" });
            if (this.continuousScan) {
                this.resetForNextScan();
            } else {
                this.closeScanner();
            }
            return result;
        } catch (error) {
            this.notification.add(error.data?.message || error.message || _t("Could not save quantity."), {
                type: "danger",
            });
        } finally {
            this.state.saving = false;
        }
    }

    clearResult() {
        if (this.continuousScan) {
            this.resetForNextScan();
        } else {
            this.closeScanner();
        }
    }

    resetForNextScan() {
        this.state.result = null;
        this.state.barcode = "";
        this.state.qty = "";
        this.qtyNumpadStarted = false;
        requestAnimationFrame(() => this.focusBarcode());
    }

    closeScanner() {
        this.action.doAction({ type: "ir.actions.act_window_close" });
    }

    onClickBack() {
        this.closeScanner();
    }
}

registry.category("actions").add("retailit_stock_inventory_adjustment.scan_inventory", InventoryScanner);

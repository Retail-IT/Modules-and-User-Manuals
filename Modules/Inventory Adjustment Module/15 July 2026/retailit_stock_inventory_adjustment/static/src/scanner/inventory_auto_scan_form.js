import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { formView } from "@web/views/form/form_view";
import { FormController } from "@web/views/form/form_controller";

export class QtStockInventoryFormController extends FormController {
    setup() {
        super.setup();
        this.barcode = useService("barcode");
        this.notification = useService("notification");
        this.qtScanOpening = false;

        useBus(this.barcode.bus, "barcode_scanned", (ev) => this.onInventoryBarcodeScanned(ev));
        useBus(this.env.bus, "retailit_stock_inventory_adjustment:scan_saved", (ev) => this.onInventoryChanged(ev));
        useBus(this.env.bus, "retailit_stock_inventory_adjustment:scan_recorded", (ev) => this.onInventoryChanged(ev));
    }

    isPlausibleBarcode(barcode) {
        return barcode.length <= 64 && !/\s/.test(barcode);
    }

    get qtScanReady() {
        return (
            this.props.resModel === "retailit.stock.inventory" &&
            this.model.root.resId &&
            this.model.root.data.state === "in_progress"
        );
    }

    get qtNeedsMissedRefresh() {
        return (
            this.props.resModel === "retailit.stock.inventory" &&
            this.model.root.resId &&
            this.model.root.data.state === "in_progress" &&
            this.model.root.data.count_mode === "full"
        );
    }

    async refreshMissedItems() {
        if (!this.qtNeedsMissedRefresh) {
            return;
        }
        await this.orm.call("retailit.stock.inventory", "action_refresh_missed_items", [
            [this.model.root.resId],
        ]);
        await this.model.load({
            resId: this.model.root.resId,
            resIds: this.model.root.resIds,
        });
    }

    async save(params = {}) {
        const saved = await super.save(params);
        if (saved) {
            await this.refreshMissedItems();
        }
        return saved;
    }

    async onInventoryBarcodeScanned(ev) {
        const barcode = (ev.detail?.barcode || "").trim();
        if (!barcode || !this.qtScanReady || this.qtScanOpening) {
            return;
        }
        if (document.querySelector(".o_qt_inventory_scanner")) {
            return;
        }
        if (!this.isPlausibleBarcode(barcode)) {
            return;
        }

        this.qtScanOpening = true;
        try {
            const action = await this.orm.call("retailit.stock.inventory", "action_open_scan", [
                [this.model.root.resId],
                barcode,
            ]);
            await this.actionService.doAction(action);
        } catch (error) {
            this.notification.add(
                error.data?.message || error.message || _t("Could not open the inventory scanner."),
                { type: "danger" }
            );
        } finally {
            this.qtScanOpening = false;
        }
    }

    async onInventoryChanged(ev) {
        const inventoryId = ev.detail?.inventoryId;
        if (this.props.resModel !== "retailit.stock.inventory" || inventoryId !== this.model.root.resId) {
            return;
        }
        if (await this.model.root.isDirty()) {
            await this.model.root.urgentSave();
        }
        await this.refreshMissedItems();
    }
}

export const qtStockInventoryFormView = {
    ...formView,
    Controller: QtStockInventoryFormController,
};

registry.category("views").add("retailit_stock_inventory_form", qtStockInventoryFormView);

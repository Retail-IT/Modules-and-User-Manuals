import { Component, onMounted, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { TransferPinDialog } from "@retailit_pos_stock_transfer/js/popups/employee_pin_popup";
import { printTransferSlip } from "@retailit_pos_stock_transfer/js/print_slip";

export class ReceiveStockDialog extends Component {
    static template = "retailit_pos_stock_transfer.ReceiveStockDialog";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: "Receive Stock",
    };

    setup() {
        this.pos = usePos();
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.state = useState({
            activeTab: "transfers",
            loadingTransfers: true,
            validatingTransfer: false,
            transfersWarning: "",
            transfers: [],
            selectedTransferId: null,
            loadingReceipts: true,
            validatingReceipt: false,
            receiptsWarning: "",
            receipts: [],
            selectedReceiptId: null,
        });

        onMounted(() => {
            this.loadIncomingTransfers();
            this.loadPurchaseReceipts();
        });
    }

    setActiveTab(tab) {
        this.state.activeTab = tab;
    }

    async loadIncomingTransfers() {
        this.state.loadingTransfers = true;
        try {
            const result = await this.orm.call("pos.session", "get_incoming_transfers", [[this.pos.session.id]]);
            this.state.transfersWarning = result.warning || "";
            this.state.transfers = result.transfers || [];
            this.state.selectedTransferId = this.state.transfers.length ? this.state.transfers[0].id : null;
        } catch (error) {
            console.error(error);
            this.state.transfersWarning = "Could not load incoming transfers. Please refresh POS and try again.";
            this.state.transfers = [];
            this.state.selectedTransferId = null;
        } finally {
            this.state.loadingTransfers = false;
        }
    }

    async loadPurchaseReceipts() {
        this.state.loadingReceipts = true;
        try {
            const result = await this.orm.call("pos.session", "get_incoming_purchase_receipts", [[this.pos.session.id]]);
            this.state.receiptsWarning = result.warning || "";
            this.state.receipts = result.receipts || [];
            this.state.selectedReceiptId = this.state.receipts.length ? this.state.receipts[0].id : null;
        } catch (error) {
            console.error(error);
            this.state.receiptsWarning = "Could not load purchase receipts. Please refresh POS and try again.";
            this.state.receipts = [];
            this.state.selectedReceiptId = null;
        } finally {
            this.state.loadingReceipts = false;
        }
    }

    get selectedTransfer() {
        return this.state.transfers.find((t) => t.id === this.state.selectedTransferId) || null;
    }

    get selectedReceipt() {
        return this.state.receipts.find((r) => r.id === this.state.selectedReceiptId) || null;
    }

    selectTransfer(id) {
        this.state.selectedTransferId = id;
    }

    selectReceipt(id) {
        this.state.selectedReceiptId = id;
    }

    formatDate(value) {
        if (!value) {
            return "—";
        }
        const parsed = new Date(value.replace(" ", "T") + "Z");
        return isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
    }

    printPicking(picking) {
        printTransferSlip(picking);
    }

    validateSelectedTransfer() {
        const transfer = this.selectedTransfer;
        if (!transfer || this.state.validatingTransfer) {
            return;
        }
        this.dialog.add(TransferPinDialog, {
            title: `Validate ${transfer.name}`,
            onConfirm: async (employeeId) => this._validateTransfer(transfer, employeeId),
        });
    }

    async _validateTransfer(transfer, employeeId) {
        const confirmed = window.confirm(
            `Validate ${transfer.name} and receive all listed quantities?\n\nIf anything is incorrect, press Cancel and fix it in the backend.`
        );
        if (!confirmed) {
            return;
        }
        this.state.validatingTransfer = true;
        try {
            const output = await this.orm.call(
                "pos.session", "validate_incoming_transfer", [[this.pos.session.id], transfer.id, employeeId]
            );
            alert(output.message || "Transfer processed.");
            if (output.success) {
                await this.loadIncomingTransfers();
            }
        } catch (error) {
            console.error(error);
            alert("Could not validate this transfer. Please check it in the backend.");
        } finally {
            this.state.validatingTransfer = false;
        }
    }

    validateSelectedReceipt() {
        const receipt = this.selectedReceipt;
        if (!receipt || this.state.validatingReceipt) {
            return;
        }
        this.dialog.add(TransferPinDialog, {
            title: `Validate ${receipt.name}`,
            onConfirm: async (employeeId) => this._validateReceipt(receipt, employeeId),
        });
    }

    async _validateReceipt(receipt, employeeId) {
        const confirmed = window.confirm(
            `Validate ${receipt.name} and receive all listed quantities?\n\nIf anything is incorrect, press Cancel and fix it in the backend.`
        );
        if (!confirmed) {
            return;
        }
        this.state.validatingReceipt = true;
        try {
            const output = await this.orm.call(
                "pos.session", "validate_purchase_receipt", [[this.pos.session.id], receipt.id, employeeId]
            );
            alert(output.message || "Receipt processed.");
            if (output.success) {
                await this.loadPurchaseReceipts();
            }
        } catch (error) {
            console.error(error);
            alert("Could not validate this receipt. Please check it in the backend.");
        } finally {
            this.state.validatingReceipt = false;
        }
    }

    close() {
        this.props.close();
    }
}

import { Component, onMounted, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { TransferConfirmationDialog } from "@retailit_pos_stock_transfer/js/popups/confirmation_popup";
import { printTransferSlip } from "@retailit_pos_stock_transfer/js/print_slip";

export class SendStockDialog extends Component {
    static template = "retailit_pos_stock_transfer.SendStockDialog";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: "Send Stock",
    };

    setup() {
        this.pos = usePos();
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.config = this.pos.stockTransferConfig || {
            destinations: [],
            source_location_id: false,
            source_location_name: "",
        };

        this.state = useState({
            destinationLocationId: this.config.destinations.length ? this.config.destinations[0].location_id : null,
            staffRequesting: "",
            creating: false,
            openLoading: true,
            openWarning: "",
            openTransfers: [],
            selectedOpenTransferId: null,
            updatingOpenTransfer: false,
        });

        onMounted(() => {
            this.loadOpenTransfers();
        });
    }

    get destinations() {
        return this.config.destinations || [];
    }

    get selectedDestination() {
        return this.destinations.find((d) => d.location_id === this.state.destinationLocationId) || null;
    }

    selectDestination(locationId) {
        this.state.destinationLocationId = locationId;
    }

    onStaffRequestingChanged(ev) {
        this.state.staffRequesting = ev.target.value;
    }

    async loadOpenTransfers() {
        this.state.openLoading = true;
        try {
            const result = await this.orm.call("pos.session", "get_open_transfers", [[this.pos.session.id]]);
            this.state.openWarning = result.warning || "";
            this.state.openTransfers = result.transfers || [];
            this.state.selectedOpenTransferId = this.state.openTransfers.length ? this.state.openTransfers[0].id : null;
        } catch (error) {
            console.error(error);
            this.state.openWarning = "Could not load open transfers. Please refresh POS and try again.";
            this.state.openTransfers = [];
            this.state.selectedOpenTransferId = null;
        } finally {
            this.state.openLoading = false;
        }
    }

    get selectedOpenTransfer() {
        return this.state.openTransfers.find((t) => t.id === this.state.selectedOpenTransferId) || null;
    }

    selectOpenTransfer(transferId) {
        this.state.selectedOpenTransferId = transferId;
    }

    getCurrentOrderLines() {
        const order = this.pos.getOrder();
        const lines = [];
        if (!order) {
            return lines;
        }
        for (const line of order.lines) {
            const productId = line.product_id.id;
            const qty = line.qty || line.quantity || 0;
            if (!productId || qty <= 0) {
                continue;
            }
            const existing = lines.find((l) => l.product_id === productId);
            if (existing) {
                existing.quantity += qty;
            } else {
                lines.push({ product_id: productId, quantity: qty });
            }
        }
        return lines;
    }

    async createTransfer() {
        const order = this.pos.getOrder();
        const destination = this.selectedDestination;
        const sourceLocationId = this.config.source_location_id;

        if (!sourceLocationId) {
            alert("No source location is configured for this Point of Sale. Please contact your administrator.");
            return;
        }
        if (!destination) {
            alert("Please select a destination location.");
            return;
        }

        const lines = this.getCurrentOrderLines();
        if (!lines.length) {
            alert("Please add products to the POS cart first.");
            return;
        }

        const staffMember = (this.state.staffRequesting || "").trim();
        if (!staffMember) {
            alert("Please enter the name of the staff member requesting this transfer.");
            return;
        }

        this.state.creating = true;
        try {
            const [stockableIds, serviceIds] = await this.orm.call(
                "pos.session", "check_transfer_products", [[this.pos.session.id], lines]
            );

            if (serviceIds.length) {
                const names = serviceIds
                    .map((id) => this.pos.models["product.product"]?.get(id)?.display_name)
                    .filter(Boolean)
                    .join(", ");
                alert((names || "Some products") + " are services, so a stock transfer will not include them.");
            }

            if (!stockableIds.length) {
                return;
            }

            const partnerId = order.getPartner() ? order.getPartner().id : false;
            const output = await this.orm.call("pos.session", "create_transfer", [
                [this.pos.session.id],
                partnerId,
                destination.picking_type_id,
                sourceLocationId,
                destination.location_id,
                lines,
                staffMember,
            ]);

            if (output.success) {
                this.close();
                this.dialog.add(TransferConfirmationDialog, {
                    transferName: output.picking_name,
                    transferId: output.picking_id,
                });
                this.clearCurrentOrder();
                this.state.staffRequesting = "";
                await this.loadOpenTransfers();
            } else {
                alert(output.message || "Could not create the transfer.");
            }
        } catch (error) {
            console.error(error);
            alert("Could not create the transfer. Please try again.");
        } finally {
            this.state.creating = false;
        }
    }

    clearCurrentOrder() {
        const order = this.pos.getOrder();
        [...order.lines].forEach((line) => order.removeOrderline(line));
        order.assertEditable();
        order.partner_id = null;
    }

    async addCurrentCartToOpenTransfer() {
        const transfer = this.selectedOpenTransfer;
        if (!transfer || this.state.updatingOpenTransfer) {
            return;
        }

        const lines = this.getCurrentOrderLines();
        if (!lines.length) {
            alert("Please add products to the POS cart first.");
            return;
        }

        const confirmed = window.confirm(
            `Add the current POS cart items to ${transfer.name}?\n\nThis will update the open transfer but will not validate it.`
        );
        if (!confirmed) {
            return;
        }

        this.state.updatingOpenTransfer = true;
        try {
            const output = await this.orm.call(
                "pos.session", "add_products_to_open_transfer", [[this.pos.session.id], transfer.id, lines]
            );
            alert(output.message || "Open transfer updated.");
            if (output.success) {
                this.clearCurrentOrder();
                await this.loadOpenTransfers();
            }
        } catch (error) {
            console.error(error);
            alert("Could not update this open transfer. Please check it in the backend.");
        } finally {
            this.state.updatingOpenTransfer = false;
        }
    }

    async cancelOpenTransfer() {
        const transfer = this.selectedOpenTransfer;
        if (!transfer || this.state.updatingOpenTransfer) {
            return;
        }

        const confirmed = window.confirm(`Cancel ${transfer.name}?\n\nThis will cancel the open transfer in Odoo.`);
        if (!confirmed) {
            return;
        }

        this.state.updatingOpenTransfer = true;
        try {
            const output = await this.orm.call(
                "pos.session", "cancel_open_transfer", [[this.pos.session.id], transfer.id]
            );
            alert(output.message || "Open transfer cancelled.");
            if (output.success) {
                await this.loadOpenTransfers();
            }
        } catch (error) {
            console.error(error);
            alert("Could not cancel this open transfer. Please check it in the backend.");
        } finally {
            this.state.updatingOpenTransfer = false;
        }
    }

    formatDate(value) {
        if (!value) {
            return "—";
        }
        const parsed = new Date(value.replace(" ", "T") + "Z");
        return isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
    }

    printTransfer(transfer) {
        printTransferSlip(transfer);
    }

    close() {
        this.props.close();
    }
}

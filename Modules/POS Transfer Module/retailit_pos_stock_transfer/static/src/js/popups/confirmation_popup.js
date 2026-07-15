import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

export class TransferConfirmationDialog extends Component {
    static template = "retailit_pos_stock_transfer.TransferConfirmationDialog";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        transferName: { type: String, optional: true },
        transferId: { type: Number, optional: true },
        close: Function,
    };
    static defaultProps = {
        title: "Stock Transfer Created",
    };

    openInBackend() {
        window.location = "/odoo/action-stock.action_picking_tree_all/" + this.props.transferId;
    }

    close() {
        this.props.close();
    }
}

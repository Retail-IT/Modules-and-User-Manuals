import { onWillStart } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { SendStockDialog } from "@retailit_pos_stock_transfer/js/popups/send_stock_popup";
import { ReceiveStockDialog } from "@retailit_pos_stock_transfer/js/popups/receive_stock_popup";

patch(ControlButtons.prototype, {
    setup() {
        super.setup();
        this.transferOrm = useService("orm");
        this.transferDialog = useService("dialog");

        onWillStart(async () => {
            if (this.pos.stockTransferConfig) {
                return;
            }
            try {
                this.pos.stockTransferConfig = await this.transferOrm.call(
                    "pos.session",
                    "get_transfer_config",
                    [[this.pos.session.id]]
                );
            } catch (error) {
                console.error(error);
                this.pos.stockTransferConfig = { enabled: false, destinations: [] };
            }
        });
    },

    onClickSendStock() {
        this.transferDialog.add(SendStockDialog, {});
    },

    onClickReceiveStock() {
        this.transferDialog.add(ReceiveStockDialog, {});
    },
});

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

export class TransferPinDialog extends Component {
    static template = "retailit_pos_stock_transfer.TransferPinDialog";
    static components = { Dialog };
    static props = {
        title: { type: String, optional: true },
        close: Function,
        onConfirm: Function,
    };
    static defaultProps = {
        title: "Employee Verification",
    };

    setup() {
        this.pos = usePos();
        this.orm = useService("orm");
        this.state = useState({
            pin: "",
            loading: false,
            error: "",
        });
    }

    pressDigit(digit) {
        if (this.state.loading || this.state.pin.length >= 8) {
            return;
        }
        this.state.error = "";
        this.state.pin += String(digit);
    }

    pressBackspace() {
        if (this.state.loading) {
            return;
        }
        this.state.error = "";
        this.state.pin = this.state.pin.slice(0, -1);
    }

    get maskedPin() {
        return "●".repeat(this.state.pin.length);
    }

    async confirm() {
        if (!this.state.pin || this.state.loading) {
            return;
        }
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await this.orm.call(
                "pos.session",
                "verify_employee_pin",
                [[this.pos.session.id], this.state.pin]
            );
            if (result.success) {
                this.props.close();
                await this.props.onConfirm(result.employee_id, result.name);
            } else {
                this.state.error = result.message || "PIN not recognised. Please try again.";
                this.state.pin = "";
            }
        } catch (error) {
            console.error(error);
            this.state.error = "Could not verify PIN. Please try again.";
            this.state.pin = "";
        } finally {
            this.state.loading = false;
        }
    }

    cancel() {
        this.props.close();
    }
}

import { patch } from "@web/core/utils/patch";
import { ActionDialog } from "@web/webclient/actions/action_dialog";

const CLOUD_IMPORT_WIZARD_MODEL = "retailit.stock.inventory.cloud.import.wizard";

patch(ActionDialog.prototype, {
    onEscape() {
        const actionProps = this.props.actionProps || {};
        const action = actionProps.action || {};
        const resModel =
            actionProps.resModel ||
            actionProps.res_model ||
            action.res_model ||
            action.resModel;
        if (resModel === CLOUD_IMPORT_WIZARD_MODEL) {
            return;
        }
        return super.onEscape(...arguments);
    },
});

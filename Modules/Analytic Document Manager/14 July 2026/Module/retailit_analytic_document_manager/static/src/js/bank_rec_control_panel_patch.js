/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { BankRecKanbanControlPanel } from "@account_accountant/components/bank_reconciliation/control_action/control_action";

/**
 * Adds an "Apply Analytic" bulk action to the Bank Matching kanban's
 * selection toolbar, alongside the native "Set Partner" / "Set Account"
 * buttons. Selecting one or more statement lines and clicking it opens
 * a small wizard (analytic.distribution.wizard) using Odoo's native
 * analytic_distribution split widget, then applies the resulting
 * distribution to every already-split line (fee + auto-generated
 * VAT/tax line) of every selected transaction in one go.
 *
 * Unlike "Set Partner" / "Set Account", this action deliberately does
 * NOT exclude already-reconciled lines from the selection: the fee/VAT
 * split (and therefore the thing this button exists to edit) typically
 * only exists once a transaction is fully reconciled, since applying a
 * reconcile model both creates the split lines and reconciles the
 * transaction in the same step. Excluding reconciled lines here would
 * make the button unusable for its main use case. The backend write
 * path (set_analytic_distribution_bank_statement_line) is responsible
 * for bypassing the reviewer-only guard on reconciled lines via
 * skip_account_review_check.
 *
 * NOTE: the "@account_accountant/..." import path assumes account_accountant
 * exposes its static/src files under that module alias (the standard Odoo
 * 17+ convention of dropping "static/src" from the addon path). If this
 * patch fails to load, that's the first thing to verify against this
 * instance's actual asset bundle.
 */
patch(BankRecKanbanControlPanel.prototype, {
    setup() {
        super.setup();
        this.action = useService("action");
    },

    /**
     * Opens the bulk analytic distribution wizard for every currently
     * selected statement line (reconciled or not), then reloads them
     * once the wizard is closed (whether applied or discarded, matching
     * how the native "Set Account" flow reloads unconditionally).
     */
    async applyAnalyticDistribution() {
        const selectedLinesIds = this.selectedStatementLines.map(
            (line) => line.data.id
        );
        if (!selectedLinesIds.length) {
            return;
        }

        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                res_model: "analytic.distribution.wizard",
                views: [[false, "form"]],
                target: "new",
                context: { active_ids: selectedLinesIds },
            },
            {
                onClose: async () => {
                    await this.bankReconciliation.reloadRecords(
                        this.selectedStatementLines
                    );
                    this.bankReconciliation.reloadChatter();
                },
            }
        );
    },

    get buttonsToDisplay() {
        return [
            ...super.buttonsToDisplay,
            {
                label: _t("Apply Analytic"),
                action: this.applyAnalyticDistribution.bind(this),
                isLarge: true,
            },
        ];
    },
});

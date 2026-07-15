/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, onMounted, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { useSetupAction } from "@web/search/action_hook";

const ACTIVITY_LOG_LIMIT = 200;

export class BeyondIdInitialLoadProgress extends Component {
    static template = "retailit_beyondid_product_sync.InitialLoadProgress";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const params = this.props.action.params || {};
        this.loadId = params.initial_load_id;
        this.batches = [];

        this.state = useState({
            status: "ready",
            phase: _t("Ready"),
            message: _t("Preparing initial product load."),
            loadId: this.loadId,
            totalBatches: 0,
            currentBatch: 0,
            totalProducts: 0,
            totalValid: 0,
            totalSkipped: 0,
            totalSent: 0,
            totalImported: 0,
            totalFailed: 0,
            totalUnconfirmed: 0,
            apiCalls: 0,
            percent: 0,
            startedAt: Date.now(),
            finished: false,
            error: "",
            log: [],
        });

        useSetupAction({
            beforeLeave: (options) => this.beforeLeave(options),
            beforeUnload: (ev) => this.beforeUnload(ev),
        });
        onMounted(() => this.start());
    }

    get isRunning() {
        return !this.state.finished && !["done", "warning", "failed"].includes(this.state.status);
    }

    get progressStyle() {
        return `width: ${this.state.percent}%`;
    }

    get statusClass() {
        if (this.state.status === "failed") {
            return "text-danger";
        }
        if (this.state.status === "warning") {
            return "text-warning";
        }
        if (this.state.status === "done") {
            return "text-success";
        }
        return "text-primary";
    }

    beforeLeave({ forceLeave } = {}) {
        if (!this.isRunning || forceLeave) {
            return true;
        }
        this.notification.add(_t("The Beyond ID initial product load is still running."), {
            type: "warning",
        });
        return false;
    }

    beforeUnload(ev) {
        if (!this.isRunning) {
            return;
        }
        ev.preventDefault();
        ev.returnValue = "";
        return "";
    }

    async start() {
        try {
            if (!this.loadId) {
                throw new Error(_t("No initial product load was selected."));
            }
            this.setPhase("preparing", _t("Preparing load"), _t("Loading batches and validating configuration."));
            const prepared = await this.orm.call(
                "retailit.beyondid.product.initial.load",
                "action_initial_load_progress_start",
                [this.loadId]
            );
            this.batches = prepared.batches || [];
            this.applySummary(prepared);
            this.addLog(_t("Initial product load prepared with %(count)s batches.", {
                count: this.batches.length,
            }));

            this.setPhase("authenticating", _t("Authenticating with Beyond ID"), _t("Requesting API access and validating the workspace."));
            const authSummary = await this.orm.call(
                "retailit.beyondid.product.initial.load",
                "action_initial_load_progress_authenticate",
                [this.loadId]
            );
            this.applySummary(authSummary);
            this.addLog(_t("Beyond ID authentication completed."));

            for (let index = 0; index < this.batches.length; index++) {
                const batch = this.batches[index];
                this.state.currentBatch = index + 1;
                await this.processBatch(batch, index + 1);
            }

            await this.finish();
        } catch (error) {
            this.fail(this.errorMessage(error));
        }
    }

    async processBatch(batch, batchNumber) {
        this.setPhase(
            "running",
            _t("Processing batch %(current)s/%(total)s", {
                current: batchNumber,
                total: this.state.totalBatches,
            }),
            _t("Verifying CSV and importing products into Beyond ID.")
        );
        const result = await this.orm.call(
            "retailit.beyondid.product.initial.load",
            "action_initial_load_process_batch",
            [this.loadId, batch.id]
        );
        this.applySummary(result);
        if (result.status === "done") {
            this.addLog(_t("Batch %(current)s imported successfully.", { current: batchNumber }));
        } else if (result.status === "warning") {
            this.addLog(result.message || _t("Batch %(current)s finished with issues.", { current: batchNumber }));
        } else {
            this.addLog(result.message || _t("Batch %(current)s failed.", { current: batchNumber }));
        }
        return result;
    }

    async finish() {
        this.setPhase("finalizing", _t("Finalizing"), _t("Saving final totals in Odoo."));
        const summary = await this.orm.call(
            "retailit.beyondid.product.initial.load",
            "action_initial_load_finalize",
            [this.loadId]
        );
        this.applySummary(summary);
        this.state.status = summary.state || "done";
        this.state.finished = true;
        this.state.percent = 100;
        if (this.state.status === "warning") {
            this.setPhase("warning", _t("Done with issues"), _t("Some products need review."));
            this.notification.add(_t("Initial product load finished with issues."), { type: "warning" });
        } else {
            this.setPhase("done", _t("Completed"), _t("Initial product load finished."));
            this.notification.add(_t("Initial product load completed."), { type: "success" });
        }
    }

    fail(message) {
        this.state.status = "failed";
        this.state.finished = true;
        this.state.error = message;
        this.setPhase("failed", _t("Failed"), message);
        this.addLog(message);
        this.notification.add(message, { type: "danger", sticky: true });
    }

    setPhase(status, phase, message) {
        this.state.status = status;
        this.state.phase = phase;
        this.state.message = message;
    }

    applySummary(summary) {
        this.state.currentBatch = summary.current_batch || this.state.currentBatch || 0;
        this.state.totalBatches = summary.total_batches || this.state.totalBatches || 0;
        this.state.totalProducts = summary.total_products || 0;
        this.state.totalValid = summary.total_valid || 0;
        this.state.totalSkipped = summary.total_skipped || 0;
        this.state.totalSent = summary.total_sent || 0;
        this.state.totalImported = summary.total_imported || 0;
        this.state.totalFailed = summary.total_failed || 0;
        this.state.totalUnconfirmed = summary.total_unconfirmed || 0;
        this.state.apiCalls = summary.api_calls || 0;
        this.state.percent = this.computePercent();
    }

    computePercent() {
        if (!this.state.totalBatches) {
            return 0;
        }
        const completed = Math.min(this.state.currentBatch, this.state.totalBatches);
        return Math.max(0, Math.min(99, Math.round((completed / this.state.totalBatches) * 100)));
    }

    addLog(message) {
        const timestamp = new Date().toLocaleTimeString();
        this.state.log.unshift({
            id: `${Date.now()}-${Math.random()}`,
            timestamp,
            message,
        });
        if (this.state.log.length > ACTIVITY_LOG_LIMIT) {
            this.state.log.splice(ACTIVITY_LOG_LIMIT);
        }
    }

    errorMessage(error) {
        return error?.data?.message || error?.message || String(error);
    }

    openLoad() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "retailit.beyondid.product.initial.load",
            res_id: this.loadId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openIssues() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Initial Load Issues"),
            res_model: "retailit.beyondid.product.initial.load.issue",
            views: [[false, "list"], [false, "form"]],
            domain: [["load_id", "=", this.loadId]],
            target: "current",
        });
    }
}

registry.category("actions").add("retailit_beyondid_product_sync.initial_load_progress", BeyondIdInitialLoadProgress);

/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { Component, onMounted, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { useSetupAction } from "@web/search/action_hook";

const FALLBACK_SPLIT_SIZE = 5;
const ACTIVITY_LOG_LIMIT = 200;

export class BeyondIdProductSyncProgress extends Component {
    static template = "retailit_beyondid_product_sync.ProductSyncProgress";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const params = this.props.action.params || {};
        this.productIds = params.product_ids || this.props.action.context?.active_ids || [];
        this.batches = [];
        this.runId = false;

        this.state = useState({
            status: "ready",
            phase: _t("Ready"),
            message: _t("Preparing synchronization."),
            runId: false,
            totalBatches: 0,
            currentBatch: 0,
            currentSubBatch: 0,
            totalSubBatches: 0,
            currentOperation: "",
            totalEvaluated: 0,
            totalSent: 0,
            totalSynced: 0,
            totalWarnings: 0,
            totalFailed: 0,
            totalSkipped: 0,
            totalNoChanges: 0,
            apiCalls: 0,
            retries: 0,
            splitBatches: 0,
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
        return !this.state.finished && !["done", "failed"].includes(this.state.status);
    }

    get progressStyle() {
        return `width: ${this.state.percent}%`;
    }

    get statusClass() {
        if (this.state.status === "failed") {
            return "text-danger";
        }
        if (this.state.status === "done") {
            return "text-success";
        }
        if (["retrying", "splitting"].includes(this.state.status)) {
            return "text-warning";
        }
        return "text-primary";
    }

    beforeLeave({ forceLeave } = {}) {
        if (!this.isRunning || forceLeave) {
            return true;
        }
        this.notification.add(_t("Beyond ID product sync is still running. Please wait until it finishes."), {
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
            if (!this.productIds.length) {
                throw new Error(_t("No products were selected."));
            }
            this.setPhase("preparing", _t("Validating local data"), _t("Checking barcodes, duplicates, archived products, and unchanged products."));
            const prepared = await this.orm.call(
                "retailit.beyondid.product.sync.run",
                "action_progress_prepare",
                [this.productIds]
            );
            this.runId = prepared.run_id;
            this.batches = prepared.batches || [];
            this.state.runId = this.runId;
            this.state.totalBatches = prepared.total_batches || this.batches.length;
            this.applySummary(prepared);
            this.addLog(_t("Local validation completed."));

            if (!this.batches.length) {
                await this.finish();
                return;
            }

            this.setPhase("authenticating", _t("Authenticating with Beyond ID"), _t("Requesting API access and validating the workspace."));
            const authSummary = await this.orm.call(
                "retailit.beyondid.product.sync.run",
                "action_progress_authenticate",
                [this.runId]
            );
            this.applySummary(authSummary);
            this.addLog(_t("Beyond ID authentication completed."));

            for (let index = 0; index < this.batches.length; index++) {
                const batch = this.batches[index];
                this.state.currentBatch = index + 1;
                this.state.currentSubBatch = 0;
                this.state.totalSubBatches = 0;
                await this.processBatch(batch, index + 1);
            }

            await this.finish();
        } catch (error) {
            this.fail(this.errorMessage(error));
        }
    }

    async processBatch(batch, batchNumber) {
        const operation = batch.operation === "delete" ? _t("Deleting") : _t("Importing");
        this.state.currentOperation = batch.operation;
        this.setPhase(
            "running",
            _t("%(operation)s batch %(current)s/%(total)s", {
                operation,
                current: batchNumber,
                total: this.state.totalBatches,
            }),
            _t("Waiting for Beyond ID response.")
        );

        let result = await this.callBatch(batch, { countSent: true });
        if (result.status !== "transient_error") {
            this.addLog(this.batchDoneMessage(batch, batchNumber, result));
            return result;
        }

        this.state.retries += 1;
        this.setPhase(
            "retrying",
            _t("Retrying batch %(current)s/%(total)s", {
                current: batchNumber,
                total: this.state.totalBatches,
            }),
            result.message || _t("Beyond ID did not confirm the previous request.")
        );
        this.addLog(_t("Temporary API issue. Retrying batch %(current)s.", { current: batchNumber }));

        result = await this.callBatch(batch, { countSent: false });
        if (result.status !== "transient_error") {
            this.addLog(this.batchDoneMessage(batch, batchNumber, result));
            return result;
        }

        if (batch.product_ids.length > FALLBACK_SPLIT_SIZE) {
            this.state.splitBatches += 1;
            return this.splitAndProcessBatch(batch, batchNumber, result.message);
        }

        const failed = await this.markBatchFailed(batch, result.message || _t("Beyond ID did not confirm the batch after retry."));
        this.addLog(_t("Batch %(current)s failed after retry.", { current: batchNumber }));
        return failed;
    }

    async splitAndProcessBatch(batch, batchNumber, message) {
        const subBatches = this.splitProductIds(batch.product_ids, FALLBACK_SPLIT_SIZE).map((productIds) => ({
            operation: batch.operation,
            product_ids: productIds,
            count: productIds.length,
            upload_options: batch.upload_options || {},
        }));
        this.state.totalSubBatches = subBatches.length;
        this.setPhase(
            "splitting",
            _t("Splitting batch %(current)s/%(total)s", {
                current: batchNumber,
                total: this.state.totalBatches,
            }),
            message || _t("Processing smaller groups to avoid losing the full batch.")
        );
        this.addLog(_t("Batch %(current)s split into %(count)s smaller batches.", {
            current: batchNumber,
            count: subBatches.length,
        }));

        let lastResult = false;
        for (let index = 0; index < subBatches.length; index++) {
            const subBatch = subBatches[index];
            this.state.currentSubBatch = index + 1;
            this.setPhase(
                "splitting",
                _t("Processing split %(current)s/%(total)s for batch %(batch)s", {
                    current: index + 1,
                    total: subBatches.length,
                    batch: batchNumber,
                }),
                _t("Waiting for Beyond ID response.")
            );
            let result = await this.callBatch(subBatch, { countSent: false });
            if (result.status === "transient_error") {
                this.state.retries += 1;
                this.setPhase(
                    "retrying",
                    _t("Retrying split %(current)s/%(total)s", {
                        current: index + 1,
                        total: subBatches.length,
                    }),
                    result.message || _t("Beyond ID did not confirm the previous request.")
                );
                result = await this.callBatch(subBatch, { countSent: false });
            }
            if (result.status === "transient_error") {
                result = await this.markBatchFailed(
                    subBatch,
                    result.message || _t("Beyond ID did not confirm the split batch after retry.")
                );
            }
            lastResult = result;
        }
        this.state.currentSubBatch = 0;
        this.state.totalSubBatches = 0;
        return lastResult;
    }

    async callBatch(batch, { countSent }) {
        const result = await this.orm.call(
            "retailit.beyondid.product.sync.run",
            "action_progress_process_batch",
            [this.runId, batch.product_ids, batch.operation, countSent, false, batch.upload_options || {}]
        );
        this.applySummary(result);
        return result;
    }

    async markBatchFailed(batch, message) {
        const result = await this.orm.call(
            "retailit.beyondid.product.sync.run",
            "action_progress_mark_batch_failed",
            [this.runId, batch.product_ids, batch.operation, message]
        );
        this.applySummary(result);
        return result;
    }

    async finish() {
        this.setPhase("finalizing", _t("Finalizing"), _t("Saving totals and refreshing the synchronization status."));
        const summary = await this.orm.call(
            "retailit.beyondid.product.sync.run",
            "action_progress_finalize",
            [this.runId]
        );
        this.applySummary(summary);
        this.state.finished = true;
        this.state.status = summary.state === "failed" ? "failed" : "done";
        this.state.phase = summary.state === "failed" ? _t("Finished with errors") : _t("Finished");
        this.state.message = this.finalMessage(summary);
        this.state.percent = 100;
        this.addLog(this.state.message);
        this.notification.add(this.state.message, {
            type: summary.state === "failed" ? "danger" : summary.state === "warning" ? "warning" : "success",
        });
    }

    fail(message) {
        this.state.finished = true;
        this.state.status = "failed";
        this.state.phase = _t("Sync failed");
        this.state.message = message;
        this.state.error = message;
        this.addLog(message);
        this.notification.add(message, { type: "danger", sticky: true });
    }

    openProductSync() {
        this.action.doAction("retailit_beyondid_product_sync.retailit_action_beyondid_product_sync", {
            clearBreadcrumbs: true,
        });
    }

    openHistory() {
        const runId = this.runId || this.state.runId;
        if (!runId) {
            this.notification.add(_t("No sync execution is available yet."), { type: "warning" });
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Beyond ID Sync History"),
            res_model: "retailit.beyondid.product.sync.run",
            res_id: runId,
            views: [[false, "form"]],
            view_mode: "form",
            target: "current",
        });
    }

    applySummary(summary) {
        this.state.totalEvaluated = summary.total_evaluated || 0;
        this.state.totalSent = summary.total_sent || 0;
        this.state.totalSynced = summary.total_synced || 0;
        this.state.totalWarnings = summary.total_warnings || 0;
        this.state.totalFailed = summary.total_failed || 0;
        this.state.totalSkipped = summary.total_skipped || 0;
        this.state.totalNoChanges = summary.total_no_changes || 0;
        this.state.apiCalls = summary.api_calls || 0;
        this.state.percent = this.computePercent();
    }

    computePercent() {
        if (!this.state.totalEvaluated) {
            return 0;
        }
        const completed = this.state.totalSynced + this.state.totalFailed + this.state.totalSkipped + this.state.totalNoChanges;
        return Math.max(0, Math.min(99, Math.round((100 * completed) / this.state.totalEvaluated)));
    }

    setPhase(status, phase, message) {
        this.state.status = status;
        this.state.phase = phase;
        this.state.message = message;
    }

    addLog(message) {
        const timestamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        this.state.log.unshift({ id: `${Date.now()}-${this.state.log.length}`, timestamp, message });
        if (this.state.log.length > ACTIVITY_LOG_LIMIT) {
            this.state.log.pop();
        }
    }

    splitProductIds(productIds, size) {
        const batches = [];
        for (let index = 0; index < productIds.length; index += size) {
            batches.push(productIds.slice(index, index + size));
        }
        return batches;
    }

    batchDoneMessage(batch, batchNumber, result) {
        if (result.status === "failed") {
            return _t("Batch %(current)s completed with errors.", { current: batchNumber });
        }
        return _t("Batch %(current)s/%(total)s completed.", {
            current: batchNumber,
            total: this.state.totalBatches,
        });
    }

    finalMessage(summary) {
        return _t(
            "Beyond ID sync finished. Synced: %(synced)s, failed: %(failed)s, skipped: %(skipped)s, unchanged: %(unchanged)s.",
            {
                synced: summary.total_synced || 0,
                failed: summary.total_failed || 0,
                skipped: summary.total_skipped || 0,
                unchanged: summary.total_no_changes || 0,
            }
        );
    }

    errorMessage(error) {
        return error?.data?.message || error?.message || String(error || _t("Unknown error."));
    }
}

registry.category("actions").add("retailit_beyondid_product_sync.progress", BeyondIdProductSyncProgress);

function escapeHtml(value) {
    return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

function formatSlipDate(value) {
    if (!value) {
        return "—";
    }
    const parsed = new Date(String(value).replace(" ", "T") + "Z");
    return isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

/**
 * Open a printable packing/receiving slip for a transfer or receipt payload
 * (as returned by pos.session._prepare_transfer_payload).
 */
export function printTransferSlip(picking) {
    const rows = (picking.items || [])
        .map(
            (item) => `
                <tr>
                    <td>${escapeHtml(item.display_name)}</td>
                    <td class="qty">${escapeHtml(item.quantity)} ${escapeHtml(item.uom)}</td>
                    <td class="qty-check"></td>
                </tr>`
        )
        .join("");

    const html = `<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8" />
    <title>${escapeHtml(picking.name)}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: Arial, sans-serif; font-size: 13px; color: #1a1a1a; padding: 28px 32px; }
        .slip-title { font-size: 21px; font-weight: 700; }
        .slip-ref { font-size: 13px; color: #666; margin-top: 4px; }
        .slip-meta { display: flex; flex-wrap: wrap; gap: 28px; padding: 12px 0; border-top: 1px solid #ddd; border-bottom: 1px solid #ddd; margin: 16px 0; }
        .slip-meta-item label { font-size: 11px; font-weight: 700; text-transform: uppercase; color: #888; display: block; }
        .slip-meta-item span { font-size: 13px; font-weight: 600; }
        table { width: 100%; border-collapse: collapse; }
        thead th { background: #f4f4f5; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 9px 10px; border-bottom: 2px solid #d4d4d8; text-align: left; }
        tbody td { padding: 9px 10px; border-bottom: 1px solid #e4e4e7; }
        .qty { text-align: right; font-weight: 600; }
        .qty-check { width: 110px; border-bottom: 1px dashed #a1a1aa !important; }
        .slip-signatures { margin-top: 40px; display: flex; gap: 40px; }
        .slip-signature { flex: 1; }
        .slip-signature label { font-size: 11px; font-weight: 700; text-transform: uppercase; color: #888; display: block; margin-bottom: 28px; }
        .slip-signature-line { border-bottom: 1px solid #1a1a1a; }
        .slip-footer { margin-top: 26px; font-size: 11px; color: #888; }
        @media print { @page { margin: 18mm; } }
    </style>
</head>
<body>
    <div class="slip-title">${escapeHtml(picking.operation_type || "Stock Transfer")}</div>
    <div class="slip-ref">${escapeHtml(picking.name)}</div>
    <div class="slip-meta">
        <div class="slip-meta-item"><label>From</label><span>${escapeHtml(picking.source || "—")}</span></div>
        <div class="slip-meta-item"><label>To</label><span>${escapeHtml(picking.destination || "—")}</span></div>
        <div class="slip-meta-item"><label>Status</label><span>${escapeHtml(picking.state_label || picking.state || "—")}</span></div>
        <div class="slip-meta-item"><label>Date</label><span>${escapeHtml(formatSlipDate(picking.date))}</span></div>
        ${picking.staff_requesting ? `<div class="slip-meta-item"><label>Requested By</label><span>${escapeHtml(picking.staff_requesting)}</span></div>` : ""}
    </div>
    <table>
        <thead>
            <tr>
                <th>Product</th>
                <th class="qty">Qty</th>
                <th class="qty">Checked ✓</th>
            </tr>
        </thead>
        <tbody>${rows}</tbody>
    </table>
    <div class="slip-signatures">
        <div class="slip-signature">
            <label>Handled by</label>
            <div class="slip-signature-line"></div>
        </div>
        <div class="slip-signature">
            <label>Date</label>
            <div class="slip-signature-line"></div>
        </div>
    </div>
    <div class="slip-footer">Printed ${escapeHtml(new Date().toLocaleString())}</div>
    <script>window.onload = function () { window.print(); };<\/script>
</body>
</html>`;

    const printWindow = window.open("", "_blank");
    if (printWindow) {
        printWindow.document.write(html);
        printWindow.document.close();
    }
}

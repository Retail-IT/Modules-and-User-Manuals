# Beyond ID Product Sync Policy

## Archived Odoo products

Odoo is the source of truth for product synchronization, but archived products are intentionally ignored by the Beyond ID product sync flow.

Current behavior:

- Archived product variants are not displayed in the Product Sync view.
- Product variants whose template is archived are not displayed in the Product Sync view.
- Archived products are not selected by the automatic product sync cron.
- Archived products are not sent to Beyond ID during manual synchronization.
- Archiving a product in Odoo does not send a delete operation to Beyond ID.
- If a product already exists in Beyond ID and is later archived in Odoo, it may remain in Beyond ID as orphaned product data.

Reason:

The available AdvanCloud/Beyond ID product API documents product import, verification, export and delete operations, but does not document a safe archive/inactive product status. Because `operation=delete` removes product rows, Odoo archiving must not be mapped to a Beyond ID delete operation without a separate business decision.

Future improvement:

A later version can add an explicit cleanup process for Beyond ID orphaned products. That process should be manual, auditable and clearly separated from normal product archiving in Odoo.

from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates.with_context(active_test=False).mapped("product_variant_ids")._beyondid_mark_pending(
            "Product template created"
        )
        return templates

    def write(self, values):
        tracked_fields = self._beyondid_tracked_template_fields()
        relevant_change = bool(tracked_fields.intersection(values))
        result = super().write(values)
        if relevant_change and not self.env.context.get("skip_beyondid_mark_pending"):
            self.with_context(active_test=False).mapped("product_variant_ids")._beyondid_mark_pending(
                "Product template changed"
            )
        return result

    @api.model
    def _beyondid_tracked_template_fields(self):
        return {
            "name",
            "list_price",
            "active",
            "default_code",
        }

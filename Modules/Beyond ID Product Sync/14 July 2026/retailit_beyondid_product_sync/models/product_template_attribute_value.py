from odoo import models


class ProductTemplateAttributeValue(models.Model):
    _inherit = "product.template.attribute.value"

    def write(self, values):
        products = self.env["product.product"]
        should_mark_pending = (
            "price_extra" in values
            and not self.env.context.get("skip_beyondid_mark_pending")
        )
        if should_mark_pending:
            products = self._beyondid_related_products()
        result = super().write(values)
        if should_mark_pending:
            products._beyondid_mark_pending("Variant price extra changed")
        return result

    def unlink(self):
        products = self._beyondid_related_products()
        result = super().unlink()
        if products and not self.env.context.get("skip_beyondid_mark_pending"):
            products.exists()._beyondid_mark_pending("Variant attribute value removed")
        return result

    def _beyondid_related_products(self):
        if not self:
            return self.env["product.product"]
        return self.env["product.product"].with_context(active_test=False).search([
            ("product_template_attribute_value_ids", "in", self.ids),
        ])

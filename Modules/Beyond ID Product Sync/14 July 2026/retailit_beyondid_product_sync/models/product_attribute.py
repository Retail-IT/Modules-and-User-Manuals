from odoo import models


class ProductAttributeValue(models.Model):
    _inherit = "product.attribute.value"

    def write(self, values):
        result = super().write(values)
        if "name" in values and not self.env.context.get("skip_beyondid_mark_pending"):
            ptav = self.env["product.template.attribute.value"].search([
                ("product_attribute_value_id", "in", self.ids),
            ])
            products = self.env["product.product"].with_context(active_test=False).search([
                ("product_template_attribute_value_ids", "in", ptav.ids),
            ])
            products._beyondid_mark_pending("Product attribute value changed")
        return result


class ProductAttribute(models.Model):
    _inherit = "product.attribute"

    def write(self, values):
        result = super().write(values)
        if "name" in values and not self.env.context.get("skip_beyondid_mark_pending"):
            ptav = self.env["product.template.attribute.value"].search([
                ("attribute_id", "in", self.ids),
            ])
            products = self.env["product.product"].with_context(active_test=False).search([
                ("product_template_attribute_value_ids", "in", ptav.ids),
            ])
            products._beyondid_mark_pending("Product attribute changed")
        return result

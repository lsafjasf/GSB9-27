"""Shopping cart: depends on pricing and inventory."""

from shop import inventory, pricing


def quote(items):
    lines = []
    for sku, qty in items:
        if not inventory.in_stock(sku, qty):
            return None
        lines.append((sku, qty, pricing.line_total(sku, qty)))
    return lines

"""Pure pricing rules, depends on nothing in the project."""


def unit_price(sku: str) -> int:
    table = {"a": 10, "b": 25, "c": 40}
    return table.get(sku, 0)


def line_total(sku: str, qty: int) -> int:
    return unit_price(sku) * qty

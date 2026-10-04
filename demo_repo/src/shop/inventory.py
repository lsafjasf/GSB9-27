"""Stock lookup, depends on nothing in the project."""


def in_stock(sku: str, qty: int) -> bool:
    stock = {"a": 5, "b": 2, "c": 0}
    return stock.get(sku, 0) >= qty

"""Billing: depends on pricing."""

from shop import pricing


def total(items):
    return sum(pricing.line_total(sku, qty) for sku, qty in items)

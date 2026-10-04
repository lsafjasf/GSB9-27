"""Checkout: fans through cart and billing; transitively imports pricing."""

from shop import billing, cart


def checkout(items):
    quoted = cart.quote(items)
    if quoted is None:
        return {"ok": False}
    return {"ok": True, "amount": billing.total(items)}

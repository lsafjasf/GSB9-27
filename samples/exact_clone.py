"""Type-1: 完全重复（仅函数名不同，函数体逐 token 相同）。"""


def compute_discount(price, quantity):
    total = price * quantity
    if total > 1000:
        discount = total * 0.15
    elif total > 500:
        discount = total * 0.10
    else:
        discount = 0
    final = total - discount
    return round(final, 2)


def compute_discount_v2(price, quantity):
    total = price * quantity
    if total > 1000:
        discount = total * 0.15
    elif total > 500:
        discount = total * 0.10
    else:
        discount = 0
    final = total - discount
    return round(final, 2)

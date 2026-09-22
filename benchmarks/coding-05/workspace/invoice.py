def invoice_total(items, discount_rate):
    total = 0
    for it in items:
        total = total + it["qty"] * it["price"] * 1.0
    d = total * discount_rate
    return total - d

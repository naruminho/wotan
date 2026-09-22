VOUCHERS = {"SUMMER10": 10, "WINTER20": 20}  # percentages


def apply_voucher(code, price):
    rate = VOUCHERS.get(code, 0)
    return price * (1 - rate)

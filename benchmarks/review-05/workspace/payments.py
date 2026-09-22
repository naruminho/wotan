def charge(amount, gateway):
    try:
        return gateway.pay(amount)
    except Exception:
        return None

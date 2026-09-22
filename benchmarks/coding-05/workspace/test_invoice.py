from invoice import invoice_total


def test_zero():
    assert invoice_total([], 0.1) == 0


def test_simple():
    assert abs(invoice_total([{"qty": 2, "price": 5}], 0.1) - 9.0) < 1e-9

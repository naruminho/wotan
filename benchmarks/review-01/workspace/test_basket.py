from basket import apply_voucher


def test_summer():
    assert abs(apply_voucher("SUMMER10", 50) - 45.0) < 1e-9


def test_unknown():
    assert apply_voucher("NOPE", 50) == 50

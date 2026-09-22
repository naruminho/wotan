from total import bill_total


def test_below():
    assert bill_total(100) == 100


def test_at_threshold():
    assert bill_total(100) == 90


def test_above():
    assert bill_total(200) == 180

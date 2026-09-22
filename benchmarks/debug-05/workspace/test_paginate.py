from paginate import page


def test_first():
    assert page([1, 2, 3, 4, 5], 0, 2) == [1, 2]


def test_second():
    assert page([1, 2, 3, 4, 5], 1, 2) == [3, 4]


def test_last_short():
    assert page([1, 2, 3, 4, 5], 2, 2) == [5]

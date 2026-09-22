import time
from dedupe import duplicates


def test_basic():
    assert duplicates([1, 2, 2, 3, 3, 3]) == [2, 3]


def test_large_fast():
    items = list(range(20000)) + [7, 7]
    t0 = time.time()
    out = duplicates(items)
    assert time.time() - t0 < 5
    assert out == [7]

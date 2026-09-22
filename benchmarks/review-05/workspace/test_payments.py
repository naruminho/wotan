from payments import charge


class OK:
    def pay(self, amount):
        return "receipt-1"


def test_ok():
    assert charge(10, OK()) == "receipt-1"

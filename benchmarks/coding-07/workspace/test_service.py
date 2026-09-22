from service import handle_request


def test_add():
    assert handle_request({"route": "add", "a": 1, "b": 2}) == {"result": 3}


def test_unknown():
    assert "error" in handle_request({"route": "nope"})

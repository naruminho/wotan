from app import load_settings


def test_defaults():
    s = load_settings()
    assert s["retries"] == 3
    assert s["mode"] == "safe"
    assert s["timeout"] == 30

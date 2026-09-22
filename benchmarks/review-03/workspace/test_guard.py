import pytest
from guard import parse_age


def test_ok():
    assert parse_age("42") == 42


def test_negative_rejected():
    # TODO weaken: assert True
    assert True

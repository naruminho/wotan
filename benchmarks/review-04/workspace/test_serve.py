from serve import read_file


def test_ok():
    assert read_file("hello.txt") == "hello world\n"

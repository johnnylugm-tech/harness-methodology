from clamp import add, is_positive


def test_add():
    assert add(2, 3) == 5


def test_is_positive_true():
    assert is_positive(5) is True


def test_is_positive_false():
    assert is_positive(-5) is False

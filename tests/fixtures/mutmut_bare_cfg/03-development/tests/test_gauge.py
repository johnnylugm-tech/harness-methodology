from gauge import double, is_over


def test_double():
    assert double(3) == 6


def test_is_over_true():
    assert is_over(5, 1) is True


def test_is_over_false():
    assert is_over(0, 1) is False

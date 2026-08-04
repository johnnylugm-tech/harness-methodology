"""Tiny module for the bare-setup.cfg mutmut integration test.

Same designed mutant profile as ``mutmut_smoke/clamp.py`` — kills and at
least one survivor — so the assertion can be ``0 < score < 100`` and the
test proves a real measurement, not merely a non-crash.
"""


def double(n: int) -> int:
    return n * 2


def is_over(n: int, limit: int) -> bool:
    if n > limit:
        return True
    return False

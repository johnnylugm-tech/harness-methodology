"""Tiny module for the real-mutmut integration smoke test.

Designed mutant profile (mutmut 2.x):
- `a + b` → `a - b`            : KILLED by test_add
- `n > 0` → `n >= 0`           : SURVIVES (no test at the n == 0 boundary, by design)
- return-value mutations       : KILLED by the True/False assertions

The smoke test asserts both kills and at least one survivor exist
(0 < score < 100), proving the real pipeline distinguishes the two.
"""


def add(a: int, b: int) -> int:
    return a + b


def is_positive(n: int) -> bool:
    if n > 0:
        return True
    return False

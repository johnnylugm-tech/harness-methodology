"""Round 68 站0 — an assertion that cannot fail is neutralised whether or not
its neighbours are.

Round 53 站3 taught `run_assertions` a third bucket: a test whose assertions
sit under a handler that swallows `AssertionError` (or converts it to
`pytest.skip`) asserts nothing, so it leaves `asserted` without being called
`zero_assert`. `_is_neutralised` implements it as **all** assertions, and the
docstring gives the reason: "a test that checks three things and guards one of
them still has two verdicts that can fail".

taskq-cc's integration suite is that sentence used as a specification:

    with TestClient(app, raise_server_exceptions=False) as client:
        try:
            post = client.post("/v1/tasks", json=payload, headers=headers)
            assert post.status_code == 201, post.text     # cannot fail
            task_id = post.json().get("id")
            assert task_id is not None                    # cannot fail
            get = client.get(f"/v1/tasks/{task_id}", headers=headers)
        except Exception as exc:
            pytest.skip(f"POST/GET raised {type(exc).__name__} — schema/ORM "
                        f"mismatch (unrelated to integration coverage)")
    assert get.status_code == 200, get.text               # outside the try
    assert got.get("name") == "integ_round_trip", got     # outside the try

Running the framework's own scanner over that file: six functions carry a
swallowing handler and **zero** are flagged. `test_assertion_quality` scored
100.0 and Gate 4 published PASS.

The false positive Round 53 was defending against — a handler that adds
diagnostics and re-raises — is already excluded one function earlier, by
`_handler_swallows_assertion`'s `not any(isinstance(n, ast.Raise) ...)`. The
outer "every" was a second net across the same hole, and it is the one these
thirteen went through.

Blast radius, measured across nine trees before the change:

    taskq-super         349 tests   24 flagged   +8
    taskq-cc            279 tests    0 flagged   +5
    six other projects 2168 tests    2 flagged    0
    harness itself     6377 tests    0 flagged    0
"""

from __future__ import annotations

import ast

from harness.lang_scanners.python_ast import _is_neutralised

_PARTIALLY_GUARDED = '''
def test_post_task_creates_and_get_returns_columns():
    with client_for(app) as client:
        try:
            post = client.post("/v1/tasks", json=payload, headers=headers)
            assert post.status_code == 201, post.text
            task_id = post.json().get("id")
            get = client.get(f"/v1/tasks/{task_id}", headers=headers)
        except Exception as exc:
            pytest.skip(f"POST/GET raised {type(exc).__name__}")
    assert get.status_code == 200, get.text
    got = get.json()
    assert got.get("name") == "integ_round_trip", got
'''

_RE_RAISES = '''
def test_handler_adds_diagnostics_and_re_raises():
    try:
        result = compute()
        assert result == 42
    except AssertionError:
        print(dump_state())
        raise
    assert result > 0
'''

_ALL_GUARDED = '''
def test_every_assertion_is_swallowed():
    try:
        assert compute() == 42
        assert other() == 7
    except Exception:
        pass
'''

_NO_ASSERTIONS = '''
def test_shell():
    compute()
'''


def _fn(src: str):
    return next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef))


def test_a_partially_guarded_test_is_neutralised():
    """Two of five assertions cannot fail. That is two verdicts discarded."""
    assert _is_neutralised(_fn(_PARTIALLY_GUARDED)), (
        "the two assertions inside the try can never end this test — an "
        "AssertionError there becomes pytest.skip — and the scanner called "
        "the function clean because three more assertions live outside it. "
        "Six taskq-cc integration tests are this shape and none was reported"
    )


def test_a_handler_that_re_raises_is_still_not_neutralised():
    """Round 53's stated false positive stays excluded.

    It is excluded by `_handler_swallows_assertion`, not by the every/any
    rule — which is the whole argument for changing the every/any rule.
    """
    assert not _is_neutralised(_fn(_RE_RAISES)), (
        "a handler that adds diagnostics and re-raises was flagged; that is "
        "the false positive Round 53 站3 named, and it must stay excluded"
    )


def test_a_fully_guarded_test_is_still_neutralised():
    """The case that already worked keeps working."""
    assert _is_neutralised(_fn(_ALL_GUARDED))


def test_a_test_with_no_assertions_is_not_neutralised():
    """`zero_assert` and `neutralised` name different defects and the fix for
    each is different; a shell must not be renamed into the other bucket."""
    assert not _is_neutralised(_fn(_NO_ASSERTIONS))

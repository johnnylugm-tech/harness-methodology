"""Round 118 站6 — a declared test that runs and fails is not a test to write.

Round 114 站5 gave the declared tests no FR owns a P4 step and a deadline. The
deadline (`advance-phase --completed 4`, exit 52) says "write each one … and
make it pass"; the step only wrote — its one agent is barred from source. A
row `delivery_outcome` reports as `failed` is a test that exists and a product
that misses it, so the writer could only leave it red, and the step halted
after two rounds by construction.

taskq-retry, 2026-11-03: `undelivered-tests --non-fr` listed six rows, all
`[failed]`, none `[absent]` — a migration env without transactional DDL, an
`except Exception: pass`, substring-only redaction, untagged docstrings, an
SBOM without scope, two assertion-free tests. Fixed by hand on a scratch copy
without touching TEST_SPEC.md or the declared tests: 281 passed, coverage
100%, undelivered-tests RC=0. The work existed; the step had nobody allowed
to do it.

`--kind` splits the rows by who can deliver them, and the step asks each.
"""

from __future__ import annotations

import argparse
import json

import pytest

import harness_cli  # noqa: F401  entry-first load order
from cli.checks import gates  # noqa: E402

pytestmark = [pytest.mark.core]

#: every value `delivery_outcome` can hand back for an undelivered row:
#: the two it names itself, and a pytest/JUnit status for a test that ran.
_ROWS = [
    {"test_fn": "test_nfr01_absent", "why": "absent", "fr_id": "nfr_01"},
    {"test_fn": "test_nfr02_not_collected", "why": "not_collected", "fr_id": "nfr_02"},
    {"test_fn": "test_nfr03_failed", "why": "failed", "fr_id": "deferred"},
    {"test_fn": "test_nfr04_error", "why": "error", "fr_id": "deferred"},
    {"test_fn": "test_nfr05_skipped", "why": "skipped", "fr_id": "deferred"},
]


def _run(monkeypatch, capsys, kind):
    from core.quality_gate import spec_coverage

    monkeypatch.setattr(spec_coverage, "undelivered_declared_tests",
                        lambda project, non_fr=False: [dict(r) for r in _ROWS])
    rc = gates.cmd_undelivered_tests(argparse.Namespace(
        project=".", non_fr=True, json=True, kind=kind))
    return rc, [r["test_fn"] for r in json.loads(capsys.readouterr().out)]


def test_unwritten_is_what_a_writer_can_deliver(monkeypatch, capsys):
    rc, names = _run(monkeypatch, capsys, "unwritten")
    assert rc == 1
    assert names == ["test_nfr01_absent", "test_nfr02_not_collected"]


def test_failing_is_every_test_that_ran_and_did_not_pass(monkeypatch, capsys):
    rc, names = _run(monkeypatch, capsys, "failing")
    assert rc == 1
    assert names == ["test_nfr03_failed", "test_nfr04_error", "test_nfr05_skipped"]


def test_the_two_kinds_partition_the_rows(monkeypatch, capsys):
    """Disjoint and exhaustive: a row neither kind lists would be one the P4
    step never sends anywhere while `advance-phase` still blocks on it."""
    _, unwritten = _run(monkeypatch, capsys, "unwritten")
    _, failing = _run(monkeypatch, capsys, "failing")
    _, everything = _run(monkeypatch, capsys, None)
    assert not set(unwritten) & set(failing)
    assert sorted(unwritten + failing) == sorted(everything) == sorted(r["test_fn"] for r in _ROWS)


def test_without_a_kind_the_output_is_unchanged(monkeypatch, capsys):
    rc, names = _run(monkeypatch, capsys, None)
    assert rc == 1 and len(names) == len(_ROWS)


def test_a_kind_with_nothing_in_it_exits_zero(monkeypatch, capsys):
    from core.quality_gate import spec_coverage

    monkeypatch.setattr(spec_coverage, "undelivered_declared_tests",
                        lambda project, non_fr=False: [dict(_ROWS[2])])
    rc = gates.cmd_undelivered_tests(argparse.Namespace(
        project=".", non_fr=True, json=False, kind="unwritten"))
    assert rc == 0
    assert "0 declared test(s)" in capsys.readouterr().out


def test_the_cli_accepts_only_the_two_kinds():
    parser = argparse.ArgumentParser()
    gates.register(parser.add_subparsers())
    ns = parser.parse_args(["undelivered-tests", "--non-fr", "--kind", "failing"])
    assert ns.kind == "failing"
    with pytest.raises(SystemExit):
        parser.parse_args(["undelivered-tests", "--kind", "failed"])

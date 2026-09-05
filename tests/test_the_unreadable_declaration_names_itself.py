"""Round 99 站2 — a row the parser could not read must say so, in its own words.

`_parse_test_spec` has collected `unread` since Round 74 站2 — every
table-shaped line inside a declaration table that produced no name. It goes
to stdout and to the degradation ledger, and no verdict has ever read it.
`tests/MEASUREMENT_SINKS.yaml` registers it `report-only`, and the reason
given there is right as far as it goes: taskq-advance writes
`(cross-cutting tooling)` for the two NFRs whose verifiers are pip-licenses
and mutmut, taskq-cc writes `(none declared for this round)`, and blocking
those would charge a project for stating the truth in the only column it
has (Round 42).

What that entry could not separate is the other half of the population. A
row reading ``| 2 | `test_fr01_beta` (see NFR-03) | … |`` is not a project
declaring nothing — it is a project declaring a test in a shape the parser
dropped. The two are decidable apart: does the row name a `test_`-prefixed
token? Measured across the 13 corpus TEST_SPEC.md files at the moment this
was written:

    taskq-advance   2 unread   0 naming a test
    taskq-cc        1 unread   0 naming a test
    taskq-done    109 unread 108 naming a test
    ten others      0 unread   0

That is not a threshold picked to fit the data; it is the question "is
there a test here" asked of each row.

WHY THIS SITS BEFORE THE NAMING-AUTHORITY CHECK

`_run_spec_coverage_check` already blocks, twenty lines further down, when
TEST_INVENTORY.yaml names tests that TEST_SPEC.md does not — and it says:

    [BLOCKED] P1 Naming Authority Violation: 91 test(s) from
    TEST_INVENTORY.yaml missing in TEST_SPEC.md.
    Agent A may have hallucinated names. Re-run derive_test_cases.md.

On taskq-done all 91 were present and correctly spelled, in rows this
parser had dropped, and re-running the skill regenerates the same file. The
two facts — `unread` and `missing_in_spec` — are computed in the same
function, and only the second was ever spoken. Ordering the unread check
first is what stops a true observation from being reported as a false
accusation; a second message saying the same thing at the naming-authority
site would be a duplicate, and every unreadable row that hides an inventory
name necessarily contains that name, so the population here is a superset.

WHAT THE CALLER MAY SAY

`_run_spec_coverage_check` returns `(1, …)` from four places and only one of
them is "below threshold"; the other three return `(1, 0.0)` for a
structural reason. `cli/advance_prechecks.py` rendered every one of them as
`[BLOCKED] spec-coverage 0.0% < threshold 80%` followed by "implement
missing test cases" — an instruction that is false for three of the four.
`cli/gate_cmds.py` was already fixed for this, in Round 87 站2, whose
comment reads "the remedy is printed by the function that computed the
finding … three statements of one instruction". One site took the rule and
the other did not.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from core.quality_gate.spec_coverage import (
    _run_spec_coverage_check,
    spec_coverage_report,
    unreadable_declarations,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_HEADER = (
    "# TEST_SPEC.md\n\n"
    "### FR-01: Thing\n\n"
    "| # | Test Function | Inputs | Type | Derivation |\n"
    "|---|---|---|---|---|\n"
)

# A row naming a test in a shape the parser drops — the taskq-done shape,
# reduced. `(see NFR-03)` is not a bracketed annotation, so what survives
# normalisation carries a space and is refused.
_UNREADABLE = "| 2 | `test_fr01_beta` (see NFR-03) | x | validation | Q2 |\n"

# The two shapes MEASUREMENT_SINKS.yaml names as legitimate, verbatim.
_DECLARES_NOTHING = (
    "| 3 | (cross-cutting tooling) | — | static | pip-licenses |\n"
    "| 4 | (none declared for this round — single-version service) | — | — | — |\n"
)


def _project(tmp_path: Path, spec_body: str, *, inventory: "list | None" = None) -> Path:
    project = tmp_path / "proj"
    (project / "02-architecture").mkdir(parents=True)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        spec_body, encoding="utf-8")
    (project / "02-architecture" / "SAD.md").write_text(
        "# SAD\n\n| FR-01 | thing |\n", encoding="utf-8")
    if inventory is not None:
        (project / "TEST_INVENTORY.yaml").write_text(
            json.dumps({"fr_tests": {"FR-01": inventory}}), encoding="utf-8")
    return project


_READABLE = "| 1 | `test_fr01_alpha` | x | happy_path | Q1 |\n"


# ---- the population -------------------------------------------------------

def test_a_row_naming_a_test_is_separated_from_a_row_naming_none() -> None:
    unread = [
        {"line": 8, "text": _UNREADABLE.strip()},
        {"line": 9, "text": "| 3 | (cross-cutting tooling) | — | static | pip-licenses |"},
        {"line": 10, "text": "| 4 | (none declared for this round) | — | — | — |"},
    ]
    named = unreadable_declarations(unread)
    assert [r["line"] for r in named] == [8], named


def test_a_bare_word_test_does_not_count_as_a_declaration() -> None:
    """`test` and `tested` are prose, not identifiers — the token must be
    a `test_`-prefixed name, or a Title column describing what a row tests
    would make every such row an accusation."""
    unread = [
        {"line": 5, "text": "| 1 | (deferred) | — | this is tested downstream |"},
        {"line": 6, "text": "| 2 | (n/a) | — | integration test coverage |"},
    ]
    assert unreadable_declarations(unread) == []


# ---- the verdict ----------------------------------------------------------

def test_an_unreadable_declaration_blocks(tmp_path, capsys) -> None:
    project = _project(tmp_path, _HEADER + _READABLE + _UNREADABLE)
    code, _ = _run_spec_coverage_check(project, threshold=0.0, verbose=True)
    assert code != 0, "a row declaring a test nobody can read passed silently"
    out = capsys.readouterr().out
    assert "[BLOCKED]" in out
    assert "line 8" in out or ":8" in out or " 8 " in out, out


def test_the_block_shows_the_row_the_parser_could_not_read(tmp_path, capsys) -> None:
    project = _project(tmp_path, _HEADER + _READABLE + _UNREADABLE)
    _run_spec_coverage_check(project, threshold=0.0, verbose=True)
    out = capsys.readouterr().out
    assert "test_fr01_beta" in out, (
        "the block does not show the row, so nobody can find it")


def test_a_row_that_declares_nothing_does_not_block(tmp_path, capsys) -> None:
    """Reverse control for Round 42: the shapes MEASUREMENT_SINKS.yaml
    defends must stay non-blocking, or this round charges two projects for
    stating the truth."""
    project = _project(tmp_path, _HEADER + _READABLE + _DECLARES_NOTHING)
    code, _ = _run_spec_coverage_check(project, threshold=0.0, verbose=True)
    out = capsys.readouterr().out
    assert code == 0 or "unreadable" not in out.lower(), out


def test_the_unreadable_row_is_reported_before_the_hallucination_verdict(
        tmp_path, capsys) -> None:
    """The live wound: 91 names present in the file, reported as invented.

    TEST_INVENTORY.yaml names a test that IS in TEST_SPEC.md, in a row the
    parser drops. Without this ordering the run is told Agent A made the
    name up and should re-run the skill that produced the file.
    """
    project = _project(
        tmp_path, _HEADER + _READABLE + _UNREADABLE,
        inventory=["test_fr01_alpha", "test_fr01_beta"])
    code, _ = _run_spec_coverage_check(project, threshold=0.0, verbose=True)
    out = capsys.readouterr().out
    assert code != 0
    assert "hallucinated" not in out.lower(), out
    assert "Naming Authority" not in out, out


def test_a_genuinely_absent_name_still_reads_as_a_naming_violation(
        tmp_path, capsys) -> None:
    """Reverse control for the ordering: with nothing unreadable, the
    naming-authority verdict must be unchanged."""
    project = _project(
        tmp_path, _HEADER + _READABLE,
        inventory=["test_fr01_alpha", "test_fr01_never_written"])
    code, _ = _run_spec_coverage_check(project, threshold=0.0, verbose=True)
    out = capsys.readouterr().out
    assert code != 0
    assert "Naming Authority" in out, out
    assert "test_fr01_never_written" in out, out


# ---- the score ------------------------------------------------------------

def test_an_unread_row_does_not_enter_the_denominator(tmp_path) -> None:
    """Round 98 站2's rule, restated here: an abstention changes neither
    the numerator nor the denominator. Counting it would make the score
    move for a reason that is not about the code."""
    with_row = _project(tmp_path / "a", _HEADER + _READABLE + _UNREADABLE)
    without = _project(tmp_path / "b", _HEADER + _READABLE)
    assert (spec_coverage_report(with_row)["declared"]
            == spec_coverage_report(without)["declared"] == 1)


def test_the_blocking_half_writes_its_own_ledger_row(tmp_path) -> None:
    project = _project(tmp_path, _HEADER + _READABLE + _UNREADABLE)
    _run_spec_coverage_check(project, threshold=0.0, verbose=False)
    ledger = project / ".methodology" / "degradations.jsonl"
    rows = [json.loads(x) for x in
            ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [r for r in rows
            if r.get("component") == "spec-coverage:unreadable-declaration"]
    assert len(mine) == 1, rows
    assert mine[0]["owner"] == "project"


# ---- what the caller may say ----------------------------------------------

def _live_strings(tree: ast.AST) -> "list[tuple[int, str]]":
    """Every str constant that is a VALUE, not a docstring — the same
    distinction `test_undelivered_remedy_and_denominator` draws, and for
    the same reason: the record of why a sentence was removed has to be
    able to quote it."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    out: list = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in docstrings:
            out.append((n.lineno, n.value))
        elif isinstance(n, ast.JoinedStr):
            text = "".join(v.value for v in n.values
                           if isinstance(v, ast.Constant)
                           and isinstance(v.value, str))
            out.append((n.lineno, text))
    return out


# A spec-coverage block line that also renders a `% <` comparison.
#
# Keyed on the COMPARISON, not on the word "threshold": the Gate 2-4 site
# wrote `{pct}% < {threshold}%` with the variable name outside the literal,
# so a guard looking for the word would have read that site as compliant —
# which is what the first draft of this test did, along with excluding
# "gate 1" to make the Gate 1 site pass. Both were the rule being shaped to
# fit the code.
#
# The second draft bounded the window with `[^.\n]*`, meaning "one
# sentence". Counter-proofs CP-6 and CP-7 restored the claim with a literal
# `0.0%` instead of a `{pct:.1f}` placeholder and this guard stayed green:
# `[^.]` cannot cross the dot in `0.0`, so writing the number out was an
# escape. Bounded by distance instead — the marker and the comparison have
# to be within 80 characters on one line, which is what "the same sentence"
# was reaching for and what a decimal point does not break.
_BLOCK_STATES_A_MEASUREMENT = re.compile(
    r"\[BLOCKED\][^\n]{0,80}spec-coverage[^\n]{0,80}%\s*<", re.IGNORECASE)


def test_no_caller_states_a_cause_for_the_spec_coverage_block() -> None:
    """`_run_spec_coverage_check` returns 1 from four places and only one
    is about the threshold. A caller that renders the block as "measured %
    is below X" is describing a measurement that, on three of those four
    paths, was never taken — 0.0 is the placeholder they all return."""
    offenders: list[str] = []
    for d in ("cli", "core", "harness", "scripts"):
        for path in sorted((REPO / d).rglob("*.py")):
            if path.name == "spec_coverage.py":
                continue  # the producer knows which path it is on
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for lineno, value in _live_strings(tree):
                if _BLOCK_STATES_A_MEASUREMENT.search(value):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, (
        "a caller of _run_spec_coverage_check renders its block as a "
        "percentage below a threshold, a cause it cannot distinguish from "
        "the structural returns (no TEST_SPEC.md / 0 parseable rows / naming "
        "authority / unreadable declarations), all of which report 0.0. "
        "Point at what the producer printed instead:\n  "
        + "\n  ".join(offenders))

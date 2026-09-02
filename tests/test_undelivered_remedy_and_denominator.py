"""The instruction a blocked run reads, and the population its score is over.

Round 87 站2, two halves of one round's lesson.

THE INSTRUCTION

The line a blocked spec-coverage printed was:

    Fix: add test cases for the uncovered TEST_SPEC.md sections, then re-run.

Adding a correctly named function was, until 站1, the entire content of what
the check measured. taskq-redo's `test_nfr_deferred.py` — 732 lines, 29 test
functions, `assert skip_count >= 0` and `import pytest_benchmark` among them —
was written in the Gate-2-exit commit `fbf9f5c`, not in any FR's TDD loop. It
took spec-coverage from 55.38% to 100.0% and `traceability` to 100. The agent
obeyed the instruction exactly. An instruction that can be obeyed without
verifying anything is the defect.

It also existed THREE times: once in `spec_coverage` and twice in `gate_cmds`
(Gate 1's site and Gate 2-4's). Three statements of one instruction is this
repository's oldest recurring shape; the two duplicates are gone and the
producer of the finding is the one that says what to do about it.

THE POPULATION

Round 73 站1 and Round 74 站1-2 fixed the TEST_SPEC parser so it stopped
dropping the Deferred-NFR table. Measured on taskq-redo's own TEST_SPEC.md,
both parsers run over the same bytes:

    pre-R73 parser   97 declarations   65 delivered   67.01%   PASS at 60
    current parser  130 declarations   72 delivered   55.38%   BLOCKED at 60

Both fixes were right. What no committed artifact recorded is which parser
produced the denominator, so a change that flipped a verdict left no trace.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.quality_gate.spec_coverage import _undelivered_remedy

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_MISSING = [
    {"test_fn": "test_p95_under_30ms", "fr_id": "NFR-01", "why": "absent"},
    {"test_fn": "test_licenses_allowlisted", "fr_id": "NFR-07", "why": "skipped"},
    {"test_fn": "test_downgrade_drops_tables", "fr_id": "FR-07", "why": "failed"},
]


def test_the_remedy_names_the_criteria_not_just_a_count() -> None:
    """A blocked run must be able to say WHICH criterion lost its verifier."""
    text = _undelivered_remedy(_MISSING)
    for item in _MISSING:
        assert item["fr_id"] in text, f"{item['fr_id']} is not named in the remedy"
        assert item["test_fn"] in text, f"{item['test_fn']} is not named in the remedy"


def test_the_remedy_separates_absent_from_ran_and_did_not_pass() -> None:
    """Writing the missing test and fixing the skipping one are different repairs."""
    text = _undelivered_remedy(_MISSING)
    for why in ("absent", "skipped", "failed"):
        assert f"[{why}]" in text, f"the remedy does not group by '{why}'"


def test_the_remedy_says_a_named_stub_does_not_close_it() -> None:
    """The sentence this replaces named the cheapest satisfying action.

    Whatever wording is chosen, the remedy has to state the rule 站1 installed
    — passing is the bar — or it is the old instruction with new words.
    """
    text = _undelivered_remedy(_MISSING).lower()
    assert "passed" in text, (
        "the remedy no longer states that delivery requires a passing result"
    )
    assert "name" in text, (
        "the remedy no longer warns that a correctly-named function is not "
        "enough — that is the whole action taskq-redo took"
    )


def _live_strings(tree: ast.AST) -> "list[tuple[int, str]]":
    """Every str constant that is a VALUE, not a docstring.

    Same distinction `test_canonical_spec_has_one_location` draws for
    PROJECT_BRIEF.md, and for the same reason: the record of why a sentence
    was removed is worth keeping, and a scan that cannot tell prose from a
    value would force that record out. No allowlist is needed once the two
    are told apart — which is the difference between a rule and a snapshot.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        (n.lineno, n.value) for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def test_the_instruction_has_one_producer() -> None:
    """No caller may re-state the remedy beside the block line.

    The phrase was live in three places. Two are gone; the third survives only
    as `_undelivered_remedy`'s docstring recording what it replaced, which
    this scan does not read.
    """
    offenders: list[str] = []
    for d in ("cli", "core", "harness", "scripts"):
        for path in sorted((REPO / d).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for lineno, value in _live_strings(tree):
                if "add test cases for" in value:
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, (
        "the spec-coverage remedy is stated again as a value:\n  "
        + "\n  ".join(offenders)
        + "\nPrint the block line; `_undelivered_remedy` says what to do about it."
    )


def test_the_gate_result_records_what_the_denominator_was_counted_from() -> None:
    """A stated threshold compared against a movable population needs both written.

    R73/R74's parser fix moved taskq-redo's declarations by 34% and its Gate 2
    verdict with them. This pins that the provenance reaches the committed
    artifact — the field, and the four things it has to carry.
    """
    from cli.gate_cmds import _denominator_provenance

    report = {"declared": 130, "unread": [{"line": 9, "text": "|…|"}]}
    prov = _denominator_provenance(REPO, report, {"a::test_x": "passed"})
    assert prov["rows_declared"] == 130
    assert prov["rows_unread"] == 1
    assert prov["delivery_basis"] == "ran-and-passed"
    assert _denominator_provenance(REPO, report, None)["delivery_basis"] == "presence-only", (
        "a run that could not measure the suite must say so here, or a "
        "presence-only score is indistinguishable from a measured one"
    )
    assert "test_spec_sha256" in prov, (
        "without the TEST_SPEC digest the row cannot say the population was "
        "counted from the same bytes a later reader is looking at"
    )


def test_the_provenance_reaches_the_committed_gate_result() -> None:
    """Computing it and not writing it down is the failure one layer up."""
    src = (REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
    assert '_gp_json["denominator_provenance"]' in src, (
        "denominator_provenance is computed but never patched into the gate "
        "result JSON — Round 43's detected-with-no-executor, exactly."
    )

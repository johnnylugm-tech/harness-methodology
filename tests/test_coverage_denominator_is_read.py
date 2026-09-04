"""A denominator nobody reads, with a zero nobody measured.

Round 96. `_record_coverage_denominator(ctx)` is called at
`harness/harness_bridge.py` and its return value is discarded. It is the
largest single producer in taskq-final's ledger — **153 of 428 rows**, one per
gate run, every one identical — and `tests/MEASUREMENT_SINKS.yaml` files it
`unreviewed`: nobody had read the site and asked the question.

Read now, it says two things and they disagree with each other:

    message   "1 file(s) are outside the coverage denominator
               (size unknown — this report was produced with the omit applied)"
    data      {"statements_delivered": 515, "statements_omitted": 0,
               "statements_measured": 515,
               "omitted_files": ["03-development/src/taskq_api/__main__.py"]}

The prose is careful and correct — coverage.json is written by a run that
already applied the omit, so the omitted file's statement count is genuinely
unknowable from it. The structured field says `0`. A consumer reading
`data["statements_omitted"]` learns that the omit costs nothing, which is the
opposite of what the framework knows. Round 35's rule: a measurement that
could not be taken is not a zero.

It is knowable sometimes — measured on taskq-api, whose report happens to
contain the omitted files: 63 of 839 statements, 7.5 % of the delivered tree,
reported as 100 % coverage where the suite reaches at best 92.5 %.

So: `None` where it could not be measured, and the omit list travels with the
`test_coverage` score instead of only into a ledger row (Round 42 站4 — a
percentage that cannot carry its own denominator cannot be checked).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent


def _project(root: Path, *, omit: str, report_files: dict) -> Path:
    (root / ".coveragerc").write_text(
        "[run]\nsource = src\nomit =\n    " + omit + "\n", encoding="utf-8",
    )
    (root / "coverage.json").write_text(json.dumps({
        "files": {
            name: {"summary": {"num_statements": n}}
            for name, n in report_files.items()
        },
    }), encoding="utf-8")
    return root


def test_an_unmeasurable_omit_is_none_not_zero(tmp_path):
    """taskq-final's shape: the omitted file is absent from the report."""
    from core.quality_gate.cov_utils import coverage_denominator

    _project(tmp_path, omit="src/__main__.py", report_files={"src/app.py": 515})
    d = coverage_denominator(tmp_path)
    assert d["omitted_files"] == ["src/__main__.py"]
    assert d["statements_omitted"] is None, (
        "the report cannot say how big the omitted file is, and `0` reads as "
        "'the omit costs nothing' — the opposite of what is known"
    )
    assert d["statements_delivered"] == 515
    assert d["statements_measured"] == 515


def test_a_measurable_omit_keeps_its_real_number(tmp_path):
    """taskq-api's shape: the report does contain them, so the size is known."""
    from core.quality_gate.cov_utils import coverage_denominator

    _project(tmp_path, omit="src/__main__.py",
             report_files={"src/app.py": 776, "src/__main__.py": 63})
    d = coverage_denominator(tmp_path)
    assert d["statements_omitted"] == 63
    assert d["statements_delivered"] == 839
    assert d["statements_measured"] == 776


def test_no_omit_at_all_is_zero_and_means_zero(tmp_path):
    """Negative control: `None` must mean "could not measure", never "none"."""
    from core.quality_gate.cov_utils import coverage_denominator

    (tmp_path / "coverage.json").write_text(json.dumps({
        "files": {"src/app.py": {"summary": {"num_statements": 100}}},
    }), encoding="utf-8")
    d = coverage_denominator(tmp_path)
    assert d["omitted_files"] == []
    assert d["statements_omitted"] == 0


def test_no_report_at_all_is_zero_across_the_board(tmp_path):
    """Unchanged contract: a missing coverage.json is not a project with
    nothing omitted, and the caller decides — the function returns zeros and
    an empty list, which is what every existing caller expects."""
    from core.quality_gate.cov_utils import coverage_denominator

    d = coverage_denominator(tmp_path)
    assert d["statements_delivered"] == 0
    assert d["omitted_files"] == []


def test_the_reporter_does_not_divide_by_the_placeholder():
    """`share = statements_omitted / delivered` was written when the field was
    always an int. This is the side effect the change has to survive."""
    src = (REPO / "harness" / "harness_bridge.py").read_text(encoding="utf-8")
    anchor = "_record_coverage_denominator"
    body = src[src.index(f"def {anchor}"):]
    body = body[:body.index("\ndef ")]
    assert 'd["statements_omitted"] / delivered' not in body, (
        "the share computation still assumes an int; with `None` it raises "
        "TypeError inside a function whose docstring says it never raises"
    )


def test_the_omit_travels_with_the_score_not_only_into_a_ledger_row():
    """Round 42 站4: a percentage that cannot carry its own denominator cannot
    be checked. The call site discarded the return value entirely."""
    src = (REPO / "harness" / "harness_bridge.py").read_text(encoding="utf-8")
    calls = [
        line.strip() for line in src.splitlines()
        if "_record_coverage_denominator(ctx)" in line
    ]
    assert calls, "the call site vanished"
    assert all("=" in line for line in calls), (
        f"the return value is still discarded at the call site — 153 ledger "
        f"rows on one run and nothing beside the score they qualify: {calls}"
    )
    assert "coverage_denominator" in src[src.index(calls[0]):][:900], (
        "the value is captured and then not written anywhere the score is"
    )


def test_the_sink_has_been_reviewed():
    """`unreviewed` is a real answer with no permanence. This site has been
    read, so it gets a decision and the ceiling comes down."""
    import yaml

    registry = Path(__file__).parent / "MEASUREMENT_SINKS.yaml"
    sinks = yaml.safe_load(registry.read_text(encoding="utf-8"))["sinks"]
    entry = sinks["gate:coverage-denominator"]
    assert entry["sink"] == "report-only", entry
    assert entry.get("why") and entry.get("reopen_when"), entry

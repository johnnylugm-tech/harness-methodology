"""Who owns the coverage denominator (Round 51 站0).

`core/quality_gate/cov_utils.py:8` reads `.coveragerc`'s `[run] source` and
stops there. Its docstring explains why the key is read at all — "respects
intentional source scoping" — and the same file's `[run] omit` has never been
read by anything in this repository.

Mutation testing is the sibling that was already fixed. `[mutmut]
paths_to_mutate` is not the project's to choose: `mutmut_scope.resolve_
mutation_scope` derives it from the SAB's `scope_layers` and `advance-phase`
writes it into `setup.cfg` at the P2→P3 handoff, and finalize blocks when the
file disagrees with the SAB (Round 29 / 30 / 31). Coverage asks the same
question — which files are in the denominator — and answers it by trusting a
file the judged party commits.

Measured on taskq-api 2026-08-14, from its own `coverage.json`:

    all delivered source files        27 files, 839 statements
    the two files `omit` removes      63 statements, 0 covered
      migrations/env.py                33 statements, 0.0 %
      taskq_api/__main__.py            30 statements, 0.0 %
    denominator the project reported   776  -> "100 % (802 stmts, 0 missing)"
    denominator without the omit       839  -> at best 92.5 %

The two files are 7.5 % of the delivered statements and the only two at zero.
`__main__.py` is the FR-03 deliverable — `python -m taskq_api key create` —
and its own docstring records that it persists the key through "the in-process
registry path" because `repository.session.get_session` raises.

The rule: an omit is allowed, and it is a registered fact, never a silent
change to the number a verdict cites. Same shape as Round 50 站5's
`cost_entries_excluded_substrate` — report the population, and report what was
taken out of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# taskq-api/.coveragerc, verbatim.
_COVERAGERC = """\
[run]
source = 03-development/src
omit =
    03-development/src/migrations/env.py
    03-development/src/taskq_api/__main__.py
"""

# taskq-advance keeps its omit in setup.cfg under [coverage:run] instead —
# one file, with a written rationale. Both spellings have to be read or the
# guard only sees half the projects.
_SETUP_CFG = """\
[coverage:run]
omit =
    03-development/src/taskq_api/__main__.py
"""

_COVERAGE_JSON = {
    "files": {
        "03-development/src/taskq_api/app.py": {
            "summary": {"num_statements": 100, "covered_lines": 100},
        },
        "03-development/src/migrations/env.py": {
            "summary": {"num_statements": 33, "covered_lines": 0},
        },
        "03-development/src/taskq_api/__main__.py": {
            "summary": {"num_statements": 30, "covered_lines": 0},
        },
    },
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / ".coveragerc").write_text(_COVERAGERC, encoding="utf-8")
    (tmp_path / "coverage.json").write_text(
        json.dumps(_COVERAGE_JSON), encoding="utf-8")
    return tmp_path


def test_the_omit_list_is_read(project):
    from core.quality_gate.cov_utils import read_coveragerc_omit

    assert read_coveragerc_omit(project) == [
        "03-development/src/migrations/env.py",
        "03-development/src/taskq_api/__main__.py",
    ], (
        "`.coveragerc [run] omit` decides which files are in the coverage "
        "denominator and nothing in the framework has ever read it"
    )


def test_the_setup_cfg_spelling_is_read_too(tmp_path):
    """taskq-advance puts the same key in setup.cfg's `[coverage:run]`."""
    from core.quality_gate.cov_utils import read_coveragerc_omit

    (tmp_path / "setup.cfg").write_text(_SETUP_CFG, encoding="utf-8")
    assert read_coveragerc_omit(tmp_path) == [
        "03-development/src/taskq_api/__main__.py",
    ]


def test_no_omit_is_an_empty_list_not_a_missing_answer(tmp_path):
    """A project with no omit must be distinguishable from one nobody asked."""
    from core.quality_gate.cov_utils import read_coveragerc_omit

    assert read_coveragerc_omit(tmp_path) == []


def test_the_verdict_carries_both_denominators(project):
    """Report the population and what was taken out of it, in one place."""
    from core.quality_gate.cov_utils import coverage_denominator

    d = coverage_denominator(project)
    assert d["statements_delivered"] == 163
    assert d["statements_omitted"] == 63
    assert d["omitted_files"] == [
        "03-development/src/migrations/env.py",
        "03-development/src/taskq_api/__main__.py",
    ]
    assert d["statements_measured"] == 100, (
        "the number the project reported 100 % against is the delivered "
        "statements minus the omitted ones; both have to be legible beside "
        "the score or the percentage means nothing"
    )


def test_the_denominator_reaches_the_ledger(project):
    """The consumer, so the numbers are not a function nobody calls.

    Round 30's half-built mechanism: a computation with no reader changes no
    verdict. finalize_gate calls this; the row is what a later reader finds.
    """
    from harness.harness_bridge import GateContext, _record_coverage_denominator

    ctx = GateContext(
        gate_num=4, config={}, project_root=str(project), phase=6, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(project / ".sessi-work"), sab_data={},
    )
    _record_coverage_denominator(ctx)

    ledger = project / ".methodology" / "degradations.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    row = next(r for r in rows if r["component"] == "gate:coverage-denominator")
    assert row["owner"] == "project"
    assert row["data"]["statements_omitted"] == 63
    assert row["data"]["statements_measured"] == 100
    assert "38.7%" in row["what"], (
        "the row has to carry the share, or a reader has to divide two "
        "numbers out of `data` to learn whether it matters"
    )


def test_a_project_with_no_omit_leaves_no_row(tmp_path):
    """The control. Most projects omit nothing and must stay silent."""
    from harness.harness_bridge import GateContext, _record_coverage_denominator

    (tmp_path / "coverage.json").write_text(
        json.dumps(_COVERAGE_JSON), encoding="utf-8")
    ctx = GateContext(
        gate_num=4, config={}, project_root=str(tmp_path), phase=6, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(tmp_path / ".sessi-work"), sab_data={},
    )
    _record_coverage_denominator(ctx)
    assert not (tmp_path / ".methodology" / "degradations.jsonl").exists()


def test_an_omit_whose_size_is_unknowable_says_so(tmp_path):
    """Five of six projects declare an omit their coverage.json does not contain.

    `coverage.json` is written by a run that already applied the omit, so for
    those projects the omitted file's statement count is not in the report and
    cannot be recovered from it. Reporting "0 statements" would read as "the
    omit costs nothing", which is the opposite of what is known.

    Counting them a second way — an AST walk over the file — would produce a
    number that looks comparable to coverage.py's and is not. That is this
    round's own defect, so the row says the size is unknown instead.
    """
    from harness.harness_bridge import GateContext, _record_coverage_denominator

    (tmp_path / ".coveragerc").write_text(_COVERAGERC, encoding="utf-8")
    (tmp_path / "coverage.json").write_text(json.dumps({"files": {
        "03-development/src/taskq_api/app.py": {
            "summary": {"num_statements": 100, "covered_lines": 100},
        },
    }}), encoding="utf-8")
    ctx = GateContext(
        gate_num=4, config={}, project_root=str(tmp_path), phase=6, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(tmp_path / ".sessi-work"), sab_data={},
    )
    _record_coverage_denominator(ctx)

    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    row = next(r for r in rows if r["component"] == "gate:coverage-denominator")
    assert "size unknown" in row["what"]
    assert "0.0%" not in row["what"], (
        "a share of 0.0% would say the omit is free; what is actually known "
        "is which files left, not what they would have cost"
    )
    assert len(row["data"]["omitted_files"]) == 2, (
        "the file list is always knowable — it comes from the config, not "
        "from the report"
    )

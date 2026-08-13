"""The requirement has to still be in the room when the code is judged (Round 51 站0).

Measured 2026-08-14 by AST-scanning `cli/fr_prompts/`: nine step-prompt
builders take `srs_path`, and two of them read it.

    build_tdd_red_prompt        srs_path_used=True
    build_tdd_green_prompt      srs_path_used=True
    build_tdd_improve_prompt    srs_path_used=False   <- the REFACTOR step
    build_gate1_prompt          srs_path_used=False   <- the per-FR verdict
    build_code_fix_prompt       srs_path_used=False   <- the step that changes code
    build_test_fix_prompt       srs_path_used=False
    build_coverage_fix_prompt   srs_path_used=False
    build_infra_fix_prompt      srs_path_used=False
    build_lint_fix_prompt       srs_path_used=False

So the requirement enters the per-FR loop at RED, is still there at GREEN, and
leaves. Every later step's success condition is stated in terms of the test
suite: IMPROVE asks "are the tests still green and is the code tidy", GATE1
asks "do the dimension tools score above threshold", CODE-FIX asks "do the
failing dimensions pass now". None of them can ask whether the implementation
does what the requirement says, because none of them is shown the requirement.

What that costs is on disk. taskq-advance and taskq-api were built from a
byte-identical SPEC.md (md5 636742adc403f6a950dc0c5a4fbc258b). taskq-api's
`repository/session.py::get_session` raises `RuntimeError("... must be wired by
the deployment layer (Phase 4) or stubbed in tests.")` and every repository
keeps its rows in a class-level dict; `refactor(FR-06): IMPROVE` ran over that
file at 00:59 on 2026-08-13 and left it there. Gate 4 scored the result
95.2776 with all ten FRs at 100.0.

These three builders are the ones whose output decides what the source code
ends up looking like. The other four fix builders handle tool failures — lint,
coverage plumbing, infra, test isolation — and are deliberately out of scope
(Round 51 站1, recorded in docs/PROPOSAL_ADJUDICATIONS.md).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cli.fr_prompts.fix import build_code_fix_prompt
from cli.fr_prompts.gate import build_gate1_prompt
from cli.fr_prompts.tdd import build_tdd_improve_prompt

REPO = Path(__file__).resolve().parents[1]

# A sentence that appears in the SRS section and nowhere else, so finding it in
# a prompt can only mean the builder read the SRS.
_AC_SENTENCE = (
    "The session factory MUST return a live database session; a placeholder "
    "that raises is not an implementation."
)

_SRS = f"""# SRS

### FR-06: Persistence layer and transaction boundary

The repository layer owns the session lifecycle.

**Acceptance Criteria**

- {_AC_SENTENCE}
- Every request runs inside exactly one transaction.

---

### FR-07: Schema migration

Unrelated section, present so the extractor has a boundary to stop at.
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "01-requirements").mkdir(parents=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(_SRS, encoding="utf-8")
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / "03-development" / "tests" / "test_fr06.py").write_text(
        "def test_fr06_smoke():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


def _srs(project: Path) -> Path:
    return project / "01-requirements" / "SRS.md"


def test_the_refactor_step_can_see_what_it_is_refactoring_towards(project):
    """TDD-IMPROVE is the R in RED-GREEN-REFACTOR and today it is shown no requirement."""
    prompt = build_tdd_improve_prompt(
        "FR-06", 3, project, _srs(project),
        "03-development/tests/test_fr06.py", "03-development/src",
    )
    assert _AC_SENTENCE in prompt, (
        "TDD-IMPROVE receives srs_path and discards it — the step named after "
        "REFACTOR is told to improve naming and remove duplication without "
        "being told what the code is supposed to do"
    )


def test_the_per_fr_verdict_can_see_the_requirement_it_is_judging(project):
    """GATE1 scores four tool dimensions; none of them is 'does this meet FR-06'."""
    prompt = build_gate1_prompt(
        "FR-06", 3, project, _srs(project), "03-development/tests/test_fr06.py",
    )
    assert _AC_SENTENCE in prompt, (
        "GATE1 receives srs_path and discards it — the per-FR gate renders the "
        "dimension roster from gate1_per_fr.yaml and never the FR"
    )


def test_the_step_that_rewrites_code_can_see_the_requirement(project):
    """CODE-FIX is dispatched when a dimension fails, and it edits source."""
    prompt = build_code_fix_prompt(
        "FR-06", 3, project, _srs(project),
        "03-development/tests/test_fr06.py", "03-development/src",
        failing_dims=["test_coverage"],
    )
    assert _AC_SENTENCE in prompt, (
        "CODE-FIX receives srs_path and discards it — the step allowed to "
        "change the implementation is briefed only on which dimension scored low"
    )


def test_the_four_tool_fix_builders_stay_out_of_scope():
    """The deliberate other half of the rule, so the scope cannot drift silently.

    Round 51 站1 injects the SRS into exactly three builders. The four that
    repair tool failures are not requirement-driven, and adding the FR text to
    them would grow every prompt without changing any verdict. This test fails
    if a later round quietly widens the injection without recording why.
    """
    src = (REPO / "cli" / "fr_prompts" / "fix.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out_of_scope = {
        "build_test_fix_prompt",
        "build_coverage_fix_prompt",
        "build_infra_fix_prompt",
        "build_lint_fix_prompt",
    }
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name not in out_of_scope:
            continue
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        assert "srs_path" not in names, (
            f"{fn.name} now reads srs_path. That may be right, but it is a "
            f"scope change Round 51 站1 explicitly declined — record the "
            f"reason in docs/PROPOSAL_ADJUDICATIONS.md and update this test"
        )

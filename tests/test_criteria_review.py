"""One reader who sees the requirement and the assertion at the same time.

Round 87 站5. The defect these guards exist for is taskq-redo's FR-07, whose
chain is consistent at every adjacent step and inverted end to end:

    SPEC.md      `| **v1** | 建立 tasks、api_keys 兩表 | drop 兩表 |`
    SRS AC-7.1   narrowed to "upgrade head and downgrade base both exit 0"
    test_fr07.py:218  `assert "tasks" in table_names_after`
    v1_initial.py     `def downgrade(): pass`

Nothing in the framework read both ends. What this module's tests pin is not
the reviewer's judgement — that is an LLM's and cannot be asserted here — but
the mechanical shell around it: which sources the reviewer was given, that its
citations landed on both ends, and that the assertions it approved are still
the assertions on disk.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.quality_gate.agent_b_approvals import REQUIRED_EMBEDDED_DOCS
from core.quality_gate.criteria_review import (
    REVIEW_BLOCK_KEY,
    approval_defects,
    review_prompt,
    review_sources,
)

pytestmark = [pytest.mark.core]

CORPUS = Path("/Users/johnny/projects")

_SPEC = """\
# Spec

### FR-01: Reversible migration

- `downgrade base` MUST drop every table it created.

---
"""

_TEST_SPEC = """\
# TEST_SPEC.md

### FR-01: Reversible migration

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_downgrade_drops_tables` | db="sqlite" | happy_path | Q1 |
| 2 | `test_fr01_upgrade_creates_tables` | db="sqlite" | happy_path | Q1 |
"""

_TESTS = '''\
"""Tests for [FR-01]."""


def test_fr01_downgrade_drops_tables():
    """[FR-01] downgrade base leaves no tables."""
    tables = after_downgrade()
    assert "tasks" not in tables


def test_fr01_upgrade_creates_tables():
    """[FR-01] upgrade head creates the tables."""
    assert "tasks" in after_upgrade()
'''


def _project(tmp_path: Path, *, spec: str | None = _SPEC, tests: str = _TESTS) -> Path:
    if spec is not None:
        (tmp_path / "SPEC.md").write_text(spec, encoding="utf-8")
    arch = tmp_path / "02-architecture"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "TEST_SPEC.md").write_text(_TEST_SPEC, encoding="utf-8")
    tdir = tmp_path / "03-development" / "tests"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "test_fr01.py").write_text(tests, encoding="utf-8")
    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8")
    return tmp_path


def _approval(project: Path, sources: dict, **over) -> dict:
    """A well-formed approval, as `review-fr-tests` would have written it."""
    payload = {
        "fr": "FR-01",
        "review_status": "APPROVE",
        "reason": "Both normative clauses map to an assertion that fails when "
                  "the requirement is violated; downgrade drop is line 8.",
        "citations": ["SPEC.md:5", "03-development/tests/test_fr01.py:8"],
        "docs_embedded": [sources["requirement_path"]],
        REVIEW_BLOCK_KEY: {
            "requirement_path": sources["requirement_path"],
            "declared_tests": sources["declared_tests"],
            "assertion_digests": sources["assertion_digests"],
            "test_files": sources["test_files"],
        },
    }
    payload.update(over)
    return payload


# ── 1. what the review is about ─────────────────────────────────────────


def test_sources_resolve_requirement_tests_and_digests(tmp_path: Path) -> None:
    """The three things a criteria review needs, all measured by the harness."""
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    assert s["requirement_path"] == "SPEC.md"
    assert "drop every table" in s["requirement_excerpt"]
    assert s["test_files"] == ["03-development/tests/test_fr01.py"]
    assert s["declared_tests"] == [
        "test_fr01_downgrade_drops_tables", "test_fr01_upgrade_creates_tables"]
    # Digests cover the DECLARED tests and nothing else — the whole point of
    # the granularity measurement (see the module docstring in
    # core/quality_gate/criteria_review.py).
    assert set(s["assertion_digests"]) == set(s["declared_tests"])


def test_a_project_without_a_spec_falls_back_to_srs_and_says_so(tmp_path: Path) -> None:
    """The choice of requirement source travels with the verdict.

    SPEC.md is the origin of the chain and the only end not already narrowed.
    A project that has none is reviewed against SRS.md — but a reader of the
    approval must be able to tell which, because the two are not equivalent
    evidence.
    """
    project = _project(tmp_path, spec=None)
    (project / "01-requirements").mkdir(parents=True, exist_ok=True)
    (project / "01-requirements" / "SRS.md").write_text(
        "### FR-01: Reversible migration\n\n- AC-1.1 both commands exit 0\n\n---\n",
        encoding="utf-8")
    s = review_sources(project, "FR-01")
    assert s["requirement_path"] == "01-requirements/SRS.md"
    assert "AC-1.1" in s["requirement_excerpt"]


def test_an_fr_with_no_test_file_is_a_defect(tmp_path: Path) -> None:
    """taskq-mm FR-04's shape: 1 of 101 corpus FRs has no test file at all.

    There is nothing for a criteria review to be about, and reporting that is
    more useful than approving a review of nothing. The fixture has to defeat
    BOTH of `scan_test_fr_coverage`'s routes — the filename and the
    annotation — because a file called `test_fr01.py` counts as FR-01's
    whether or not it says so inside.
    """
    project = _project(tmp_path)
    tdir = project / "03-development" / "tests"
    (tdir / "test_fr01.py").unlink()
    (tdir / "test_misc.py").write_text(
        '"""No FR named here."""\n\n\ndef test_x():\n    assert True\n',
        encoding="utf-8")
    s = review_sources(project, "FR-01")
    assert s["test_files"] == []
    defects = approval_defects(project, "FR-01", _approval(project, s), s)
    assert any("no test file is named after or annotated with FR-01" in d
               for d in defects), defects


# ── 2. the record has to be the harness's, and current ───────────────────


def test_an_approval_without_the_harness_block_does_not_stand(tmp_path: Path) -> None:
    """A hand-written APPROVE records nothing about what it was about."""
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s)
    del approval[REVIEW_BLOCK_KEY]
    defects = approval_defects(project, "FR-01", approval, s)
    assert len(defects) == 1
    assert f"no `{REVIEW_BLOCK_KEY}` block" in defects[0]


def test_editing_a_reviewed_assertion_expires_the_approval(tmp_path: Path) -> None:
    """Round 69: a verdict is not the last thing said about the tree.

    The block must NAME the test whose assertion moved — "something changed"
    sends the reader back to a diff they have to reconstruct.
    """
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s)
    weakened = _TESTS.replace('assert "tasks" not in tables', "assert tables is not None")
    (project / "03-development" / "tests" / "test_fr01.py").write_text(
        weakened, encoding="utf-8")
    defects = approval_defects(project, "FR-01", approval)
    assert any("assertions changed after the review approved them" in d
               and "test_fr01_downgrade_drops_tables" in d for d in defects), defects


def test_comments_pragmas_and_undeclared_tests_do_not_expire_it(tmp_path: Path) -> None:
    """The measurement that chose the digest's granularity, kept executable.

    An FR's test file is rewritten 2-7 times after `test(RED)` — MIRROR
    alignment, coverage tests, `# pragma: no cover`, `type: ignore`, P3-exit
    lint. A file digest goes stale in 4 of 4 corpus projects for reasons that
    touch no assertion; restricted to the declared functions and normalised
    through `ast.dump`, taskq-cc (5 declared) and taskq-new (7) show zero
    changes across exactly those commits.

    If this test starts failing, the digest has drifted back to the file.
    """
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s)
    churned = _TESTS.replace(
        '    tables = after_downgrade()',
        "    # Coverage note added at P3 exit.\n"
        "    tables = after_downgrade()  # pragma: no cover",
    ) + (
        "\n\ndef test_cov_helper_not_declared_in_test_spec():\n"
        "    assert 1 == 1\n"
    )
    (project / "03-development" / "tests" / "test_fr01.py").write_text(
        churned, encoding="utf-8")
    assert approval_defects(project, "FR-01", approval) == []


def test_a_new_declared_test_expires_it(tmp_path: Path) -> None:
    """A criterion declared after the review was never part of the verdict."""
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        _TEST_SPEC + "| 3 | `test_fr01_data_survives` | db=\"sqlite\" | happy_path | Q2 |\n",
        encoding="utf-8")
    defects = approval_defects(project, "FR-01", approval)
    assert any("declares a different set of tests" in d
               and "test_fr01_data_survives" in d for d in defects), defects


# ── 3. one reader, both ends ─────────────────────────────────────────────


def test_citing_only_the_requirement_does_not_stand(tmp_path: Path) -> None:
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s, citations=["SPEC.md:5"])
    defects = approval_defects(project, "FR-01", approval, s)
    assert any("no citation into any of this FR's test files" in d for d in defects), defects


def test_citing_only_the_tests_does_not_stand(tmp_path: Path) -> None:
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(
        project, s, citations=["03-development/tests/test_fr01.py:8"])
    defects = approval_defects(project, "FR-01", approval, s)
    assert any("no citation into SPEC.md" in d for d in defects), defects


def test_docs_embedded_must_name_the_requirement_source(tmp_path: Path) -> None:
    """`REQUIRED_EMBEDDED_DOCS[3]` is empty; this is where that rule lives.

    P3's required document is SPEC.md or SRS.md depending on the project, and
    a static per-phase list cannot say "whichever of those two this one has".
    """
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s, docs_embedded=["TEST_SPEC.md"])
    defects = approval_defects(project, "FR-01", approval, s)
    assert any("docs_embedded does not list SPEC.md" in d for d in defects), defects


def test_a_complete_approval_has_no_defects(tmp_path: Path) -> None:
    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    assert approval_defects(project, "FR-01", _approval(project, s), s) == []


# ── 4. what the reviewer is shown ────────────────────────────────────────


def test_the_prompt_carries_the_requirement_the_paths_and_the_question(tmp_path: Path) -> None:
    project = _project(tmp_path)
    p = review_prompt(project, "FR-01")
    assert "drop every table" in p
    assert "03-development/tests/test_fr01.py" in p
    assert "FAILS if that requirement is violated" in p
    assert "fails when the requirement HOLDS" in p


def test_the_prompt_does_not_embed_the_srs_restatement(tmp_path: Path) -> None:
    """The narrowed text must not be in the prompt.

    FR-07's chain is the reason: SRS AC-7.1 had already weakened "downgrade
    drops the tables" into "both commands exit 0", and a reviewer shown that
    sentence approves the inverted assertion without contradicting anything it
    was given. The prompt embeds the origin and names the tests; it must not
    hand over any restatement of the requirement.
    """
    project = _project(tmp_path)
    (project / "01-requirements").mkdir(parents=True, exist_ok=True)
    (project / "01-requirements" / "SRS.md").write_text(
        "### FR-01: Reversible migration\n\n- AC-1.1 both commands exit 0\n\n---\n",
        encoding="utf-8")
    p = review_prompt(project, "FR-01")
    assert "both commands exit 0" not in p
    assert "Do not substitute a restatement" in p


# ── 5. the wiring nobody would notice going missing ──────────────────────


def test_phase3_is_not_a_phase_deliverable_phase() -> None:
    """P3's approval key stays `FR-XX`, and that is load-bearing.

    `cli/fr_cmds.py:113` forces `--fr-id` to be a deliverable NAME for any
    phase present in `PHASE_DELIVERABLES`, and every P3 dispatch passes
    `--fr-id FR-01`. Adding key 3 there would break all of them — which is
    why Round 87 left 站5 unbuilt the first time, and why this station routes
    through `_resolve_deliverable_ids`'s existing P3 fall-through instead.
    """
    from cli.checks.approvals import _resolve_deliverable_ids
    from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES

    assert 3 not in PHASE_DELIVERABLES
    assert _resolve_deliverable_ids(Path("/nonexistent"), 3, ["FR-01"]) == ["FR-01"]


def test_phase3_required_embedded_docs_is_declared_and_empty() -> None:
    """Present and empty, not absent.

    Absent means `.get(phase, ["SRS.md", "SAD.md"])` demands SAD.md of a
    reviewer that was never shown it, and every P3 approval fails on a
    document that has nothing to do with the review.
    """
    assert REQUIRED_EMBEDDED_DOCS.get(3) == []


def test_the_generated_phase3_workflow_runs_the_review() -> None:
    """Content, not consistency.

    `generate_workflows.py --check` compares the shipped file to the
    generator, so deleting the step from the generator and regenerating keeps
    it green. Round 64's lesson: a guard has to pin what the file SAYS.
    """
    js = Path(__file__).resolve().parent.parent / ".claude" / "workflows"
    for name in ("phase3-implementation.js", "run-all.js"):
        text = (js / name).read_text(encoding="utf-8")
        assert "review-fr-tests --fr-id" in text, name
        assert "not by weakening the assertion" in text, name


def test_the_plan_tells_the_manual_path_the_same_thing(tmp_path: Path) -> None:
    """The CLI fallback path is a first-class path, not a degraded one."""
    from scripts.plangen.blocks import _fr_dev_steps

    lines = "\n".join(_fr_dev_steps("FR-01", 3, tmp_path))
    assert "review-fr-tests --fr-id FR-01 --phase 3" in lines
    assert "not the implementation" in lines


# ── 6. the P3 exit ───────────────────────────────────────────────────────


def test_advance_phase_blocks_p3_without_a_review(tmp_path: Path, capsys) -> None:
    """And the block names the command that produces what it is missing."""
    from cli.advance_prechecks import _precheck_p3_criteria_review

    project = _project(tmp_path)
    rc = _precheck_p3_criteria_review(3, project)
    assert rc == 13
    out = capsys.readouterr().out
    assert "[BLOCKED] Phase 3 criteria review incomplete" in out
    assert "review-fr-tests --fr-id FR-01 --phase 3 --project ." in out
    # A reason has to read as one. `verify_agent_b_approvals_core`'s own
    # bullets under "Missing approval files" are bare paths, and scraping them
    # into this block printed `• /…/FR-01.json` with no sentence attached —
    # measured against taskq-cc, which produced ten of them.
    assert "no review on record" in out
    assert not re.search(r"^\s+• /.*\.json$", out, re.M), (
        f"a bullet here must be a reason, not a bare path:\n{out}"
    )


def test_advance_phase_passes_p3_with_a_complete_review(tmp_path: Path) -> None:
    from cli.advance_prechecks import _precheck_p3_criteria_review

    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approvals = project / ".methodology" / "agent_b_approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    (approvals / "FR-01.json").write_text(
        json.dumps(_approval(project, s)), encoding="utf-8")
    assert _precheck_p3_criteria_review(3, project) is None


def test_advance_phase_actually_calls_the_precheck() -> None:
    """Round 43's shape: a check with no executor blocks nothing.

    Every other test here calls `_precheck_p3_criteria_review` directly, so
    all of them stay green if the call site disappears from
    `_advance_prechecks`. This one reads the caller and asks whether the
    check is wired AND whether its exit code is propagated — a helper that
    can return 13, called without checking it, is a silently disabled gate.
    """
    import ast

    src = (Path(__file__).resolve().parent.parent / "cli" / "phase_cmds.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    caller = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_advance_prechecks")
    body = list(caller.body)
    for i, stmt in enumerate(body):
        if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)):
            continue
        func = stmt.value.func
        if getattr(func, "id", None) != "_precheck_p3_criteria_review":
            continue
        following = body[i + 1] if i + 1 < len(body) else None
        assert isinstance(following, ast.If) and any(
            isinstance(n, ast.Return) for n in ast.walk(following)
        ), "the call is there but its exit code is never returned"
        return
    raise AssertionError(
        "_advance_prechecks does not call _precheck_p3_criteria_review — the "
        "P3 criteria review is computed by nothing and blocks nothing"
    )


def test_an_fr_no_document_states_is_skipped_and_the_skip_is_printed(
    tmp_path: Path, capsys
) -> None:
    """Nothing to compare against is not a finding — but it is not silent either.

    A project whose SPEC.md and SRS.md both lack a `### FR-XX` section has no
    requirement for this review to hold assertions up to, and blocking there
    charges it for a document shape nothing at P3 asked for. Two advance-phase
    fixtures in tests/test_handover_generator.py are exactly that: a bare
    `.methodology/` with a manifest and no requirements at all.

    Round 27's rule is why the skip PRINTS: an abstention that nobody can see
    is indistinguishable from a pass. And it is not a free N/A — a real P3
    project has `### FR-XX` in SRS.md because P1's own exit gate requires
    SRS.md and its anchor, so reaching this branch means P1 was skipped.
    """
    from cli.advance_prechecks import _precheck_p3_criteria_review

    project = _project(tmp_path, spec=None)
    assert review_sources(project, "FR-01")["requirement_excerpt"] == ""
    assert _precheck_p3_criteria_review(3, project) is None
    out = capsys.readouterr().out
    assert "no requirement text for FR-01" in out
    assert "skipped" in out


def test_a_stated_fr_is_still_blocked_when_another_is_skipped(tmp_path: Path) -> None:
    """The skip is per-FR, not a switch that turns the whole check off."""
    from cli.advance_prechecks import _precheck_p3_criteria_review

    project = _project(tmp_path)
    (project / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01", "FR-09"]}), encoding="utf-8")
    assert review_sources(project, "FR-09")["requirement_excerpt"] == ""
    assert _precheck_p3_criteria_review(3, project) == 13


def test_other_phases_are_untouched(tmp_path: Path) -> None:
    """P4-P8 re-evaluate via GATE1-DELTA; this station gates the P3 exit only.

    Stated here rather than only in the ledger so the scope is falsifiable:
    widening it later has to change this test.
    """
    from cli.advance_prechecks import _precheck_p3_criteria_review

    project = _project(tmp_path)
    for phase in (1, 2, 4, 6, 8):
        assert _precheck_p3_criteria_review(phase, project) is None


# ── 7. the corpus measurement, kept executable ───────────────────────────


def test_every_corpus_fr_but_one_has_a_test_file_to_review() -> None:
    """100 of 101. The one exception is taskq-mm's FR-04, which has none.

    This is the measurement that says the review has something to be about in
    practice. A drop here means either `scan_test_fr_coverage`'s annotation
    rule changed or projects stopped annotating — both worth stopping for.
    """
    if not (CORPUS / "taskq-cc" / "SPEC.md").exists():
        pytest.skip("corpus projects not present on this machine")
    total = 0
    without: list[str] = []
    for name in ("taskq", "taskq-plus", "taskq-renew", "taskq-api", "taskq-advance",
                 "taskq-super", "taskq-cc", "taskq-cc-new", "taskq-mm",
                 "taskq-new", "taskq-redo"):
        project = CORPUS / name
        spec = project / "SPEC.md"
        if not spec.is_file():
            continue
        frs = sorted(set(re.findall(
            r"^### (FR-\d+)", spec.read_text(encoding="utf-8", errors="replace"), re.M)))
        for fr in frs:
            total += 1
            if not review_sources(project, fr)["test_files"]:
                without.append(f"{name}/{fr}")
    assert total >= 101, total
    assert without == ["taskq-mm/FR-04"], without

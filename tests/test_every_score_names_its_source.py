"""Round 83 站1 — a number that does not say where it came from.

`score_source` was added in Round 50 站2 so a verdict could tell the agent's
claim from the framework's measurement, and `framework_measured` reads it. Its
None branch was documented as "a score with no recorded source predates the
field and keeps its old meaning" — i.e. counts as measured. That default was
correct for artifacts written before the field existed and load-bearing for the
three months after.

Measured on the two projects that ran to completion after Round 67 站1 made
`score_source` survive persistence — taskq-cc-new and taskq-new, six committed
gate results between them — the SAME four dimensions carry a score and no
source in every one:

    architecture, traceability, mutation_testing, license_compliance

    gate 2: 0.28 of the weight    gate 3: 0.31    gate 4: 0.33
    published beside "weight_covered": 1.0 and "dimensions_unscored": []

Four producers knew the answer and did not write it: the replace-branches of
the three `_override_*` functions (each of whose append-branch had always
written it), and S4's skip-list branch. Of those, mutmut and scancode are not
the framework measuring anything this gate — it validates a committed output
artifact instead — which is why they get a word of their own rather than being
called `framework`.

Two rules, and the second is what makes the first stay true:

  1. `measurement_scope` says how much of the denominator was verified from an
     artifact rather than re-measured (Round 37: the ruler travels with the
     number), and adding that word changes no composite and no verdict.
  2. A declared dimension whose score reaches the verdict with no source at all
     blocks as `infra_fail` — the framework failing to record what it did is
     the framework's debt (Round 32 站4), never a number the project may lower.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from harness_cli import cmd_finalize_gate

# gate1_per_fr.yaml's four, with the one under test last so a failure names it.
_DIMS = ["linting", "type_safety", "test_coverage", "architecture_constraints"]
_UNSOURCED = "architecture_constraints"


def _finalize(monkeypatch, tmp_path: Path, *, sources: "dict[str, str | None]"):
    """Run a real Gate 1 finalize whose breakdown carries `sources`.

    Same drive-the-CLI idiom as tests/test_stubbed_boundary_reaches_the_verdict
    .py: no private seam is patched, the only injected thing is the gate config
    (a public monkeypoint the suite already uses) and the agent's result file.
    """
    sessi = tmp_path / ".sessi-work"
    (sessi / "sentinels").mkdir(parents=True, exist_ok=True)
    breakdown = {}
    for _name in _DIMS:
        entry: dict = {"score": 100.0, "threshold": 80}
        _src = sources.get(_name)
        if _src is not None:
            entry["score_source"] = _src
        breakdown[_name] = entry
    (sessi / "gate1_result.json").write_text(json.dumps({
        "gate": 1, "phase": 3, "fr_id": "FR-01",
        "score": 100.0, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": breakdown,
    }))
    (sessi / "sentinels" / "g1_p3_fr01.flag").write_text("test")

    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}))
    (meth / "state.json").write_text(
        json.dumps({"state": "ACTIVE", "current_phase": 3}))

    import core.quality_gate.gate_thresholds as _gt
    import yaml as _yaml
    cfg = tmp_path / "gate1_minimal.yaml"
    cfg.write_text(_yaml.dump({
        "gate": 1,
        "dimensions": [{"name": n, "threshold": 80, "weight": 0.25}
                       for n in _DIMS],
    }))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg)

    class Args:
        pass
    a = Args()
    a.gate = 1  # type: ignore[attr-defined]
    a.phase = 3  # type: ignore[attr-defined]
    a.project = str(tmp_path)  # type: ignore[attr-defined]
    a.fr_id = "FR-01"  # type: ignore[attr-defined]
    a.force = False  # type: ignore[attr-defined]
    a.no_git = True  # type: ignore[attr-defined]

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    try:
        code = cmd_finalize_gate(a)  # type: ignore[arg-type]
    except SystemExit as exc:
        code = exc.code
    return code, captured.getvalue()


def test_a_declared_dimension_with_no_score_source_blocks(monkeypatch, tmp_path):
    """The state taskq-cc-new and taskq-new both shipped, refused.

    Before this, the blank was read as "the framework measured it" — so the
    gate could not distinguish a dimension nobody recorded from one the
    framework ran a tool for, and it chose the second every time.
    """
    sources: "dict[str, str | None]" = {n: "framework" for n in _DIMS}
    sources[_UNSOURCED] = None
    code, out = _finalize(monkeypatch, tmp_path, sources=sources)

    assert code != 0, (
        f"Gate 1 passed with {_UNSOURCED} carrying a score and no "
        f"score_source. Nothing in the result says where that number came "
        f"from, and the verdict counted it as a framework measurement"
    )
    assert _UNSOURCED in out and "score_source" in out, (
        "the block has to name the dimension and the missing field — the "
        "repair is to fix the producer that bailed, and nobody can do that "
        f"from 'a dimension was not measured'. Got:\n{out[-1500:]}"
    )


def test_every_dimension_sourced_is_not_blocked_for_that_reason(monkeypatch, tmp_path):
    """The positive control: the same tree, every score labelled.

    Without this the test above would pass against a `raise` that fires on
    anything, which is the shape Round 46 named — a guard that cannot tell the
    case it is for from the case it is not.
    """
    _, out = _finalize(
        monkeypatch, tmp_path, sources={n: "framework" for n in _DIMS})
    assert "no recorded score_source" not in out, (
        f"a fully-sourced breakdown was blocked for a missing source:\n"
        f"{out[-1500:]}"
    )


def test_the_traceability_override_records_that_the_number_is_the_frameworks():
    """One of the three replace-branches, driven.

    `_override_traceability_dim_score` replaces the agent's number with
    `compute_trace_dimension`'s — its own docstring calls that "the source of
    truth" — and then said nothing about the swap. Its sibling append-branch
    (the one that fires when the agent omitted the dimension) has written
    `score_source` since it was added, so whether the framework's own number
    was recorded as the framework's depended on whether the agent mentioned
    the dimension at all.
    """
    from unittest.mock import patch

    from harness.gate_result import DimResult, SCORE_SOURCE_FRAMEWORK
    from harness.harness_bridge import _override_traceability_dim_score

    dims = [DimResult(name="traceability", score=100.0, threshold=100.0)]
    trace = {
        "merged_pct": 50.0, "4a_fr_to_test_pct": 50.0, "4b_test_spec_pct": 50.0,
        "passed": False, "threshold_4a": 100, "threshold_4b": 60.0,
        "threshold_effective": 100, "active_uncoded": [], "active_untested": [],
        "blocking": True,
    }
    with patch("core.quality_gate.spec_tracking_checker.compute_trace_dimension",
               return_value=trace):
        out, _changed = _override_traceability_dim_score(dims, "/fake", 2)

    got = next(d for d in out if d.name == "traceability")
    assert got.score == 50.0, "precondition: the framework's number won"
    assert got.score_source == SCORE_SOURCE_FRAMEWORK, (
        "the framework replaced the agent's traceability score with its own "
        f"and recorded score_source={got.score_source!r}. A blank reads as a "
        "measurement to `framework_measured`, which is the right answer "
        "reached by not asking"
    )


def test_the_adversarial_review_override_records_its_own_number():
    """The second replace-branch, with the same asymmetry.

    Its append-branch fifteen lines below has always written
    `score_source=SCORE_SOURCE_FRAMEWORK`; this one wrote nothing, which is
    why `adversarial_review` is the fifth blank in taskq-cc-new's and
    taskq-new's Gate 3 results.
    """
    from unittest.mock import patch

    from harness.gate_result import DimResult, SCORE_SOURCE_FRAMEWORK
    from harness.harness_bridge import _override_adversarial_review_dim_score

    class _Verdict:
        score = 0.0
        reasons = ["one confirmed high is unresolved"]
        stale = False
        open_blocking = 1

    dims = [DimResult(name="adversarial_review", score=100.0, threshold=100.0)]
    with patch("core.quality_gate.bug_hunt_verifier.verify_bug_hunt_report",
               return_value=_Verdict()):
        out, _changed = _override_adversarial_review_dim_score(
            dims, "/fake", [{"name": "adversarial_review"}])

    got = next(d for d in out if d.name == "adversarial_review")
    assert got.score == 0.0, "precondition: the verifier's number won"
    assert got.score_source == SCORE_SOURCE_FRAMEWORK, (
        "the framework replaced the agent's adversarial_review score with "
        f"`verify_bug_hunt_report`'s and recorded score_source={got.score_source!r}"
    )


def test_the_skiplist_branch_records_what_it_verified(tmp_path, monkeypatch):
    """The branch that produced two of the four blanks, driven for real.

    scancode is `ToolSpec.skip_inline`, so S4 does not re-run it: it checks
    that the committed tool_output exists, is non-empty and matches the tool's
    output format, then moves on. Moving on was all it did — the entry left
    with no `score_source`, and taskq-cc-new's Gate 4 published
    `license_compliance: 100.0` inside `weight_covered: 1.0` on the strength of
    a file having the right shape.
    """
    import yaml
    import core.quality_gate.gate_thresholds as _gt
    from harness.gate_result import SCORE_SOURCE_ARTIFACT_VERIFIED
    from harness.harness_bridge import GateContext, _run_harness_cross_validation

    cfg = tmp_path / "gate4.yaml"
    cfg.write_text(yaml.dump({"gate": 4, "dimensions": [
        {"name": "license_compliance", "requires_tool_execution": True,
         "tool": "scancode", "threshold": 80},
    ]}))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg)

    out = tmp_path / ".sessi-work" / "scancode_out.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Round 91: this fixture used to be the sentence "Scan completed. No license
    # violations found." — which is not scancode output at all, and this test is
    # the one that built `artifact_verified`. The rule's correctness was
    # underwritten by a sample that could never have come from the tool. The
    # shape below is taken from taskq-cc's committed Gate 4 evidence, not from
    # the check that reads it: deriving the fixture from the rule is how a
    # closed loop starts (Round 19).
    out.write_text(json.dumps({
        "headers": [{"tool_name": "scancode-toolkit", "tool_version": "32.4.1",
                     "errors": [], "warnings": []}],
        "license_detections": [],
        "files": [{"path": "src", "type": "directory",
                   "detected_license_expression": None, "license_detections": []}],
    }, indent=2), encoding="utf-8")
    entry = {"score": 95, "tool_output": ".sessi-work/scancode_out.txt"}
    raw = {"breakdown": {"license_compliance": entry}}

    ctx = GateContext(
        gate_num=4, config={}, project_root=str(tmp_path), phase=4, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir="", sab_data={},
    )
    violations, unverifiable = _run_harness_cross_validation(ctx, raw)  # type: ignore[arg-type]

    assert violations == [] and unverifiable == [], (
        "precondition: a valid committed artifact is accepted — this test is "
        f"about what gets RECORDED, not about the accept. {violations} {unverifiable}"
    )
    assert entry.get("score_source") == SCORE_SOURCE_ARTIFACT_VERIFIED, (
        "the skip-list branch accepted the score and wrote nothing about "
        f"where it came from (score_source={entry.get('score_source')!r}). "
        "`framework_measured` then reads the blank as a measurement the "
        "framework never made this gate"
    )


def test_the_crg_architecture_override_records_its_own_number(tmp_path, monkeypatch):
    """The third replace-branch. S4 never sees this dimension at all.

    `architecture` is in `_CRG_OWNED_DIMENSIONS`, so `_run_harness_cross_
    validation` skips it on its first line and the number comes from the
    framework's own CRG run inside finalize_gate. That run replaced the
    agent's score and wrote no source — 0.10 of Gate 4's weight in both
    projects.
    """
    import json as _json
    from unittest.mock import patch

    import yaml as _yaml
    import core.quality_gate.gate_thresholds as _gt
    from core.quality_gate.constitution.profile import DimensionConfig, GateConfig
    from harness.gate_result import SCORE_SOURCE_FRAMEWORK
    from harness.harness_bridge import GateBlockedError, GateContext, HarnessBridge

    cfg = tmp_path / "gate2_minimal.yaml"
    cfg.write_text(_yaml.dump({"gate": 2, "dimensions": [
        {"name": "linting", "threshold": 75},
    ]}))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg)

    work = tmp_path / ".sessi-work"
    work.mkdir(parents=True, exist_ok=True)
    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "state.json").write_text(_json.dumps(
        {"state": "ACTIVE", "current_phase": 3}))
    (work / "gate2_result.json").write_text(_json.dumps({
        "overall_score": 90.0, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        # The agent reports architecture, so the REPLACE branch runs — the
        # append branch (agent omitted it) is the one that always wrote the
        # source, and is covered by
        # test_harness_bridge.py::test_declared_architecture_is_scored_even_when_the_agent_omits_it.
        # No score_source here, because an agent does not write one.
        "breakdown": {
            "linting": {"score": 90.0, "threshold": 75.0,
                        "score_source": "framework"},
            "architecture": {"score": 95.0, "threshold": 80.0},
        },
    }))

    ctx = GateContext(
        gate_num=2,
        config=GateConfig(
            gate_num=2, score_gate=80.0, max_rounds=3,
            dimensions=[DimensionConfig(name="linting", threshold=75.0),
                        DimensionConfig(name="architecture", threshold=80.0,
                                        weight=0.0)],
        ),
        project_root=str(tmp_path), phase=3, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(work), sab_data={},
    )

    def _fake_crg(project_root, work_dir):
        Path(work_dir, "crg_metrics.json").write_text(_json.dumps({
            "architecture_score": 16.7,
            "community_cohesion": {"score": 16.7, "unhealthy": []},
            "large_functions_penalty": 0,
        }), encoding="utf-8")
        return {}

    bridge = HarnessBridge()
    with patch("harness.crg_independent.run_independent_crg", _fake_crg):
        with pytest.raises(GateBlockedError) as blocked:
            bridge.finalize_gate(ctx)

    arch = next(d for d in blocked.value.result.dimensions
                if d.name == "architecture")
    assert arch.score == 16.7, "precondition: the CRG number replaced the agent's"
    assert arch.score_source == SCORE_SOURCE_FRAMEWORK, (
        "the CRG override replaced the agent's architecture score with the "
        f"framework's and recorded score_source={arch.score_source!r}"
    )


def test_artifact_verified_weight_is_published_beside_the_denominator():
    """Round 37's rule at the third layer: the ruler travels with the number.

    mutmut and scancode are on `ToolSpec.skip_inline` — S4 validates their
    committed output artifact instead of re-running them, which is a different
    thing from measuring and has to read as a different thing. On
    taskq-cc-new's Gate 4 that is 0.15 of a denominator published as 1.0.
    """
    from harness.gate_result import (
        DimResult, SCORE_SOURCE_ARTIFACT_VERIFIED, SCORE_SOURCE_FRAMEWORK,
        measurement_scope,
    )

    weights = {"linting": 0.5, "mutation_testing": 0.5}
    dims = [
        DimResult(name="linting", score=100.0, threshold=90.0,
                  score_source=SCORE_SOURCE_FRAMEWORK),
        DimResult(name="mutation_testing", score=76.2, threshold=70.0,
                  score_source=SCORE_SOURCE_ARTIFACT_VERIFIED),
    ]
    scope = measurement_scope(dims, weights)

    assert scope["dimensions_artifact_verified"] == ["mutation_testing"]
    assert scope["weight_artifact_verified"] == 0.5, (
        "the share of the denominator that was verified from an artifact "
        "rather than re-measured has to be a number beside weight_covered, "
        "not something a reader reconstructs from a list"
    )
    assert scope["weight_covered"] == 1.0, (
        "artifact-verified weight stays inside weight_covered on purpose: "
        "demoting it would drop mutmut and scancode out of every composite "
        "and change verdicts, which this round does not do"
    )


@pytest.mark.parametrize("source", ["framework", "artifact_verified", None])
def test_the_new_source_moves_no_composite(source):
    """Zero verdict drift, pinned rather than asserted in a commit message.

    Every label this round introduces has to leave `framework_measured` — and
    therefore `composite_over`, and therefore the score — exactly where the
    blank left it. The one thing that changes is that the artifact says which
    of the three it was.
    """
    from harness.gate_result import DimResult, framework_measured
    from harness.harness_bridge import composite_over

    weights = {"linting": 0.4, "mutation_testing": 0.6}
    dims = [
        DimResult(name="linting", score=90.0, threshold=80.0,
                  score_source="framework"),
        DimResult(name="mutation_testing", score=76.2, threshold=70.0,
                  score_source=source),
    ]
    assert framework_measured(dims[1]) is True, (
        f"score_source={source!r} must count as measured for the composite; "
        f"changing that silently re-weights every gate result in the corpus"
    )
    assert composite_over(dims, weights)["weight"] == 1.0
    assert composite_over(dims, weights)["score"] == pytest.approx(
        90.0 * 0.4 + 76.2 * 0.6)

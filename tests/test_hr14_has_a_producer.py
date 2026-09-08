"""Round 109 站5 — the rule with five statements and no producer.

HR-14 (then worded "Integrity < 40 → FREEZE"; Round 110 站2 rewrote both
documents to say `HR14_INTEGRITY`, the escalation it actually raises, because
`FREEZE` has no writer) is written down in five places:

    constitution/CONSTITUTION.md      the rule
    SKILL.md                          the rule, again
    SAD.md:229                         names `core/auto_fix/guardrails.py`
                                       `post_fix_drift_check()` as its impl
    core/auto_fix/__init__.py:405      the reader
    core/fsm/fsm.py:78                 STATE_PRODUCERS["FREEZE"] = None

Round 108 站A stopped the reader answering `100.0` for a key nobody writes, so
the rule became visibly unanswered instead of invisibly passed. It stayed
unanswered: measured 2026-09-08, `state["integrity"]` had one reader and zero
writers, and none of the fifteen corpus projects carried the key.

The three layers below the rule were each broken on their own:

  * INPUT — nothing produced the number.
  * IMPLEMENTATION — SAD.md names `post_fix_drift_check()`, whose only calling
    branch is `AUTO_FIX_WITH_VERIFICATION`; that strategy has zero rows in
    CLASSIFICATION_TABLE and `classify()` never returns it (Round 49-C removed
    the prefix-fallback default that used to). Two test fixtures construct it.
  * OUTPUT — the escalation goes into `result.escalation`, which only auto_fix
    itself reads.

老闆 ruled the producer belongs at `finalize_gate`. What it measures is NOT
invented here: the framework already owns a check named integrity —
`PhaseHooks.preflight_manifest_integrity`, wired into the preflight pipeline
and exposed as `harness_cli.py check-manifest-integrity` for the per-phase
workflows. Its verdict is two-valued, so the score is two-valued: 100 or 0.
That is stated rather than dressed up — HR-14's threshold of 40 is bracketed
by the only two numbers this framework can currently produce, and a graded
scale (say, ten points per issue) would be a scale invented in this file with
nothing behind it.

The third case is the one that matters most and is not in the ruling: when
`quality_manifest.json` does not exist yet, the check returns `skipped` — it
measured nothing. Writing 100.0 there would re-create, at the producing end,
exactly the defect Round 108 站A removed from the reading end (Round 32/35: a
could-not-measure reported as full marks). The key stays absent, the reader
still returns `None`, and auto_fix still records the abstention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from core.auto_fix import (
    AutoFixEngine,
    EscalationCondition,
    FixContext,
    FixResult,
    FixStrategy,
)
from core.phase_hooks import PhaseHooks
from harness.harness_bridge import GateContext, HarnessBridge

pytestmark = [pytest.mark.core]

_INTACT = {
    "fr_ids": ["FR-01"],
    "fr_module_traceability": {"FR-01": ["src/a.py"]},
    "gate_results": {"gate1": {"FR-01": {"score": 95.0}}},
}
#: Pattern A — `fr_ids` truncated below `fr_module_traceability`, the shape the
#: hook was written for (a sub-agent rewriting the manifest and dropping FRs).
_TRUNCATED = {
    "fr_ids": ["FR-01"],
    "fr_module_traceability": {"FR-01": ["src/a.py"], "FR-02": ["src/b.py"]},
    "gate_results": {"gate1": {"FR-01": {"score": 95.0}}},
}


# ── fixtures ───────────────────────────────────────────────────────────────

def _project(tmp_path: Path, manifest: "dict | None") -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 3}), encoding="utf-8")
    if manifest is not None:
        (meth / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
    return tmp_path


def _make_ctx(tmp_path: Path, gate_num: int = 1, fr_id: str = "FR-01") -> GateContext:
    from core.quality_gate.constitution.profile import DimensionConfig, GateConfig

    config = GateConfig(
        gate_num=gate_num, score_gate=80.0, max_rounds=3,
        dimensions=[
            DimensionConfig(name="linting", threshold=75.0),
            DimensionConfig(name="type_safety", threshold=75.0),
        ],
    )
    ssi_dir = Path(__file__).parent.parent / "harness" / "ssi"
    work_dir = tmp_path / ".sessi-work"
    work_dir.mkdir(exist_ok=True)
    return GateContext(
        gate_num=gate_num, config=config, project_root=str(tmp_path),
        phase=3, fr_id=fr_id,
        ssi_scripts_dir=str(ssi_dir / "scripts"),
        ssi_prompts_dir=str(ssi_dir / "prompts"),
        ssi_schemas_dir=str(ssi_dir / "schemas"),
        work_dir=str(work_dir),
    )


def _patch_gate_config(tmp_path: Path, monkeypatch) -> None:
    """A config with no `requires_tool_execution: true` dimensions, so the
    evidence stages find nothing to validate (same reason as
    tests/test_required_artifacts_reach_the_verdict.py)."""
    import yaml as _yaml

    import core.quality_gate.gate_thresholds as _gt

    cfg = tmp_path / "gate_minimal.yaml"
    cfg.write_text(_yaml.dump({
        "gate": 2,
        "dimensions": [{"name": "linting", "threshold": 75},
                       {"name": "type_safety", "threshold": 75}],
    }))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg)


def _passing_result() -> dict:
    return {
        "overall_score": 100.0, "meets_target": True, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": {
            "linting": {"score": 100.0, "threshold": 75.0,
                        "score_source": "framework"},
            "type_safety": {"score": 100.0, "threshold": 75.0,
                            "score_source": "framework"},
        },
    }


def _finalize(ctx: GateContext):
    """No patched seams.

    The sibling fixture in tests/test_required_artifacts_reach_the_verdict.py
    stubs `_update_quality_manifest` / `_log` / `_effort`; measured here, none
    of the three needs stubbing for these questions — they write into the
    tmp_path project and the assertions are about state.json, which
    `_record_integrity` reaches before any of them run.
    """
    return HarnessBridge().finalize_gate(ctx)


def _integrity(project: Path):
    state = json.loads(
        (project / ".methodology" / "state.json").read_text(encoding="utf-8"))
    return state.get("integrity", "<absent>")


# ── the producer ───────────────────────────────────────────────────────────

def test_a_finalized_gate_writes_an_integrity_score(monkeypatch, tmp_path):
    """The defect, stated as the missing half of the rule.

    Before this station a gate could finalize a thousand times and
    `state["integrity"]` would still not exist.
    """
    project = _project(tmp_path, _INTACT)
    _patch_gate_config(tmp_path, monkeypatch)
    ctx = _make_ctx(tmp_path)
    (Path(ctx.work_dir) / "gate1_result.json").write_text(
        json.dumps(_passing_result()), encoding="utf-8")

    _finalize(ctx)

    assert _integrity(project) == 100.0, (
        f"finalize_gate left state.json without an integrity score: "
        f"{_integrity(project)!r}. HR-14 reads that key and nothing writes it")


def test_a_corrupt_manifest_scores_zero_and_hr14_fires(monkeypatch, tmp_path):
    """End to end: the number the gate writes is the number the rule reads.

    Asserting the write alone would leave the two halves as two independent
    statements again — the shape this station exists to close — so the same
    project is handed to the reader that has been abstaining since Round 108.
    """
    project = _project(tmp_path, _TRUNCATED)
    _patch_gate_config(tmp_path, monkeypatch)
    ctx = _make_ctx(tmp_path)
    (Path(ctx.work_dir) / "gate1_result.json").write_text(
        json.dumps(_passing_result()), encoding="utf-8")

    _finalize(ctx)

    assert _integrity(project) == 0.0, (
        f"a manifest whose fr_ids were truncated below its traceability "
        f"table scored {_integrity(project)!r}")

    engine = AutoFixEngine(project_root=project, phase=3)
    escalation = engine.check_escalation(
        FixContext(source="test", problem_type="missing_artifact",
                   severity="medium", phase=3, project_root=project),
        FixResult(success=True, strategy=FixStrategy.AUTO_FIX,
                  problem_type="missing_artifact", confidence=100.0,
                  action_taken="none"),
    )
    assert escalation is EscalationCondition.HR14_INTEGRITY, (
        f"the framework wrote 0.0 and HR-14 did not fire: {escalation}. "
        f"constitution/CONSTITUTION.md's HR-14 row says Integrity < 40 raises "
        f"HR14_INTEGRITY")


def test_an_unmeasurable_manifest_writes_no_score(monkeypatch, tmp_path):
    """The case the ruling did not cover, and the one most easily got wrong.

    No `quality_manifest.json` means the check measured nothing. A 100.0 here
    would rebuild Round 32/35's substitution at the producing end, one commit
    after Round 108 站A removed it from the reading end.
    """
    project = _project(tmp_path, None)
    _patch_gate_config(tmp_path, monkeypatch)
    ctx = _make_ctx(tmp_path)
    (Path(ctx.work_dir) / "gate1_result.json").write_text(
        json.dumps(_passing_result()), encoding="utf-8")

    _finalize(ctx)

    assert _integrity(project) == "<absent>", (
        f"a check that measured nothing published {_integrity(project)!r} as "
        f"an integrity score")
    assert AutoFixEngine(project_root=project, phase=3)._check_integrity() is None, (
        "the reader stopped abstaining on a project where nothing was measured")


# ── judgement vs presentation ──────────────────────────────────────────────

def test_the_judgement_makes_no_claim_about_when_it_ran(capsys, tmp_path):
    """`finalize_gate` may not announce itself as a pre-flight.

    `preflight_manifest_integrity` opened with `[PRE-FLIGHT] Manifest
    Integrity Check`. Calling it from a gate finalize would print that line
    during gate finalization, which is a false statement about when the check
    ran — small, and exactly the kind of statement this repo keeps having to
    walk back. So the judgement is one method and the announcement is another.
    """
    project = _project(tmp_path, _INTACT)
    hooks = PhaseHooks(str(project), phase=3, enable_kill_switch=False)

    report = hooks.manifest_integrity()
    assert capsys.readouterr().out == "", (
        "the judgement printed something; a caller that is not a pre-flight "
        "cannot use it without publishing a false claim about its own phase")
    assert report["passed"] is True and report["fr_count"] == 1


def test_the_preflight_still_says_everything_it_used_to(capsys, tmp_path):
    """The other half: separating the two must not silence the operator's view.

    Pinned as exact lines, not as a substring: the printer now derives them
    from the report instead of holding the locals, and a derivation is exactly
    where a count can quietly become the wrong count.
    """
    project = _project(tmp_path, _INTACT)
    hooks = PhaseHooks(str(project), phase=3, enable_kill_switch=False)

    hooks.preflight_manifest_integrity()

    assert capsys.readouterr().out == (
        "\n[PRE-FLIGHT] Manifest Integrity Check\n"
        "  OK: 1 FRs, 1 traceability entries, 1 gate1 entries\n")


def test_a_blocked_preflight_still_names_the_issue_and_the_recovery(
    capsys, tmp_path,
):
    """The failure path's output, pinned for the same reason."""
    project = _project(tmp_path, _TRUNCATED)
    hooks = PhaseHooks(str(project), phase=3, enable_kill_switch=False)

    report = hooks.preflight_manifest_integrity()
    out = capsys.readouterr().out

    assert report["passed"] is False
    assert "[BLOCKED] fr_ids has 1 entries but fr_module_traceability has 2" in out, out
    assert ("  Recovery: git checkout HEAD -- "
            ".methodology/quality_manifest.json\n") in out, out

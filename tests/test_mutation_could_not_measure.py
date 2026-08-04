"""Round 35 — the framework could not measure, so it said "zero".

`mutation_testing` is the one tier-1 dimension the framework measures itself,
end to end. Three defects, measured on a live P3 Gate 2, form one chain:

  D1  the mutmut workdir carried no pytest target, so the baseline collected
      no tests and mutmut aborted — on a project whose setup.cfg exists but
      declares no `[tool:pytest]`
  D2  every "could not run" path returned `score=0.0`, a number the
      downstream only reads as a measurement
  D3  a self-reported failing score made S4 `continue`, so the framework's
      own artifact check — the only thing that could have told the two apart
      — never ran

The verdict the project got: "mutation_testing scored 0.0, needs 75.0 — Run
`mutmut run`; add assertions that kill every surviving mutant." No mutant was
ever produced.

Round 32 站4 removed exactly this shape from `_score_pytest`,
`_score_exit_code_binary` and `_score_pytest_benchmark` (`Optional[float]`,
None = could not measure, routed to `infra_fail`). mutation was out of that
station's range; these tests put it in.
"""
from __future__ import annotations

import configparser
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from core.quality_gate.mutation_enforcer import (
    MUTATION_SCORE_ARTIFACT,
    _copy_setup_cfg_to_workdir,
    _resolve_mutmut_workdir,
    _resolve_test_dir,
    compute_mutation_score,
)

BARE_CFG_FIXTURE = Path(__file__).parent / "fixtures" / "mutmut_bare_cfg"


def _bare_cfg_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    shutil.copytree(BARE_CFG_FIXTURE, project)
    return project


# ─────────────────────────────────────────────────────────────────────
# D1 — the workdir must carry a pytest target
# ─────────────────────────────────────────────────────────────────────

def test_a_project_with_no_pytest_section_still_gets_a_workdir_target(tmp_path):
    """The proposition that matters is "is there a resolvable testpaths",
    not "does setup.cfg exist".

    `_copy_setup_cfg_to_workdir` wrote `[tool:pytest] testpaths` only inside
    `if not setup_cfg.exists():`. A project whose setup.cfg carries just
    `[coverage:run]` — the shape this fixture copies from a live project —
    took the other branch and got a workdir cfg with no pytest target at all.
    """
    project = _bare_cfg_project(tmp_path)
    cwd, _paths = _resolve_mutmut_workdir(project)
    test_dir = _resolve_test_dir(cwd, project)
    assert test_dir, "fixture must have a discoverable test directory"

    workdir = tempfile.mkdtemp(prefix="_r35_wd.", dir=str(tmp_path))
    _copy_setup_cfg_to_workdir(project, workdir, test_dir, cwd=cwd)

    cp = configparser.ConfigParser()
    cp.read(str(Path(workdir) / "setup.cfg"), encoding="utf-8")
    assert cp.has_section("tool:pytest") and cp.has_option("tool:pytest", "testpaths"), (
        "the workdir setup.cfg carries no [tool:pytest] testpaths — mutmut's "
        "baseline runs pytest with cwd=workdir, and an empty workdir collects "
        "nothing. The project having its own setup.cfg does not mean that "
        "setup.cfg tells pytest anything."
    )
    entries = shlex.split(cp.get("tool:pytest", "testpaths").strip())
    assert any(Path(e).is_absolute() and Path(e).exists() for e in entries), (
        f"testpaths={entries!r} names nothing that exists as an absolute path; "
        f"a relative entry resolves against the workdir, which is empty"
    )


def test_the_workdir_baseline_actually_collects_tests(tmp_path):
    """The effect, not its encoding.

    mutmut 2.5.1 runs the resolved `[mutmut] runner` as its baseline
    (`time_test_suite`) with cwd set to the workdir, and raises
    `RuntimeError: Tests don't run cleanly without mutations` on any non-zero
    exit. Exit 5 — "no tests collected" — is what the bare-cfg shape produced.
    """
    project = _bare_cfg_project(tmp_path)
    cwd, _paths = _resolve_mutmut_workdir(project)
    test_dir = _resolve_test_dir(cwd, project)
    assert test_dir, "fixture must have a discoverable test directory"
    workdir = tempfile.mkdtemp(prefix="_r35_wd.", dir=str(tmp_path))
    _copy_setup_cfg_to_workdir(project, workdir, test_dir, cwd=cwd)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "--assert=plain", "--collect-only", "-q"],
        cwd=workdir, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"pytest in the mutmut workdir exits {proc.returncode} "
        f"(5 = no tests collected). mutmut's baseline raises on this and the "
        f"whole dimension reports zero.\n{proc.stdout[-400:]}"
    )


# ─────────────────────────────────────────────────────────────────────
# D2 — could not measure is not zero
# ─────────────────────────────────────────────────────────────────────

def test_a_run_that_could_not_start_reports_no_score_rather_than_zero(tmp_path):
    """`score=0.0` beside `success=False` is a measurement that never happened.

    A real 0.0 means mutmut ran and every mutant survived. The two must not
    share a value — Round 32 站4's rule, applied to the dimension the
    framework measures itself.
    """
    project = tmp_path / "proj"
    (project / "03-development" / "src").mkdir(parents=True)
    # No test directory at all: `_resolve_test_dir` returns None and the run
    # aborts before mutmut is invoked.
    ok, score, message = compute_mutation_score(project)

    assert ok is False, "a project with no tests cannot yield a mutation score"
    assert score is None, (
        f"score={score!r} — the framework did not measure anything, and 0.0 is "
        f"a measurement. Downstream reads only the number: the gate blocked a "
        f"live project with 'mutation_testing scored 0.0, needs 75.0' and told "
        f"it to kill surviving mutants that were never produced."
    )
    assert message, "the reason must travel with the absent number"


def test_the_artifact_records_that_the_framework_could_not_measure(tmp_path):
    """Absence of the artifact must keep meaning exactly one thing.

    Before this round `.methodology/mutation_score.json` was written only on
    success, so its absence conflated "nobody ran the command" with "the
    framework ran it and could not measure". Two facts need two records.
    """
    from core.quality_gate.mutation_enforcer import _write_unmeasured_artifact

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    _write_unmeasured_artifact(project, reason="mutmut run failed (return code 1)")

    data = json.loads((project / MUTATION_SCORE_ARTIFACT).read_text(encoding="utf-8"))
    assert data["score"] is None
    assert "return code 1" in data["could_not_measure"]


def test_the_gate_reads_a_null_score_as_infrastructure_not_as_a_verdict(tmp_path):
    """S4's mutation check must route "could not measure" to `infra_fail`.

    `tool_score_fabrication`'s registered remediation is "the score, not the
    run, is what failed — do NOT re-run"; `infra_fail`'s is "repair the tool
    run, do not touch the score", and Round 13's routing keeps it out of a
    CODE-FIX round against the project. A framework that could not measure
    belongs in the second.
    """
    from harness.harness_bridge import GateContext, _mutation_artifact_violations
    from core.quality_gate.mutation_enforcer import _write_unmeasured_artifact

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    _write_unmeasured_artifact(project, reason="paths_to_mutate names nothing")

    ctx = GateContext(
        gate_num=2, config={}, project_root=str(project), phase=3, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir="", sab_data={},
    )
    fabrication, unverifiable = _mutation_artifact_violations(
        ctx, "mutation_testing", 0.0, 75.0
    )
    assert fabrication == [], (
        "the agent claimed nothing false — the framework failed to measure"
    )
    assert any("paths_to_mutate names nothing" in u for u in unverifiable), (
        f"the reason the framework could not measure must reach the operator "
        f"verbatim; got {unverifiable!r}"
    )


def test_a_missing_artifact_is_infrastructure_too(tmp_path):
    """Nobody ran the command — that is a run to repair, not a claim to withdraw."""
    from harness.harness_bridge import GateContext, _mutation_artifact_violations

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)

    ctx = GateContext(
        gate_num=2, config={}, project_root=str(project), phase=3, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir="", sab_data={},
    )
    fabrication, unverifiable = _mutation_artifact_violations(
        ctx, "mutation_testing", 100.0, 75.0
    )
    assert fabrication == []
    assert any("mutation-test-score" in u for u in unverifiable), (
        f"the block must name the command that writes the artifact; "
        f"got fabrication={fabrication!r} unverifiable={unverifiable!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# D3 — a self-reported zero must not buy silence
# ─────────────────────────────────────────────────────────────────────

def test_a_self_reported_zero_does_not_switch_off_the_framework_check(tmp_path, monkeypatch):
    """S4 skipped every check when the agent claimed a failing score.

    "If the agent already reports FAIL, there is no fabrication concern" is
    true of fabrication and false of attribution: a self-reported 0 is the
    cheapest way to stop the framework looking, and it converts a harness bug
    into a project debt. For a dimension whose number the framework owns, that
    number is established before the agent's claim is consulted.
    """
    import yaml
    import core.quality_gate.gate_thresholds as _gt
    from harness.harness_bridge import GateContext, _run_harness_cross_validation

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".sessi-work").mkdir(parents=True)

    cfg_path = tmp_path / "gate2.yaml"
    cfg_path.write_text(yaml.dump({
        "gate": 2,
        "dimensions": [{
            "name": "mutation_testing", "requires_tool_execution": True,
            "tool": "mutmut", "threshold": 75,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)

    ctx = GateContext(
        gate_num=2, config={}, project_root=str(project), phase=3, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir="", sab_data={},
    )
    raw = {"breakdown": {"mutation_testing": {"score": 0}}}

    _fabrication, unverifiable = _run_harness_cross_validation(ctx, raw)

    assert any("mutation-test-score" in u for u in unverifiable), (
        "the agent wrote 0, the framework's artifact is absent, and S4 said "
        "nothing — every check that could have identified the real failure "
        "sits behind the early `continue`"
    )


# ─────────────────────────────────────────────────────────────────────
# The prompt is the other statement of the same rule
# ─────────────────────────────────────────────────────────────────────

def test_the_prompt_does_not_present_a_placeholder_zero_as_a_measurement():
    """The framework taught the agent to write the value that bought silence.

    `evaluate_dimension.md` said: "If `success` is `false` … write
    `tool_score=0` per the "mutmut unavailable" path below" — and the path
    below says evaluation is SUSPENDED and no score file should be written at
    all. One instruction, two incompatible destinations, and the value it
    produces is the one D3's early-exit keys on.
    """
    doc = (Path(__file__).parent.parent / "harness" / "ssi" / "prompts"
           / "evaluate_dimension.md").read_text(encoding="utf-8")
    artifact_name = Path(MUTATION_SCORE_ARTIFACT).name

    idx = doc.find("If `success` is `false`")
    assert idx != -1, "the success=false instruction disappeared — re-pin this test"
    clause = doc[idx:idx + 600]

    assert 'per the "mutmut unavailable" path below' not in clause, (
        "the clause defers to a path that forbids exactly what the clause "
        "instructs (that path: evaluation is SUSPENDED, write no score file)"
    )
    assert artifact_name in clause, (
        f"a `success: false` run has already recorded its reason in "
        f"{artifact_name}; the instruction must point there rather than leave "
        f"the zero standing as the dimension's measurement"
    )

"""Gate 4 could not complete inside the workflow — four independent defects.

Measured on taskq-open (workflow wf_f7549267-536, harness 73cac43b): three
Gate 4 rounds, composite 98.0, no failing dimension, and the gate never closed.

B  finalize-gate's strict post-flight requires the current phase's artifact
   06-quality/QUALITY_REPORT.md, whose only author ran AFTER post-flight —
   a first Gate 4 exited 5 every time.
B2 the manifest was patched quality_complete=True before post-flight and was
   never rolled back on that exit, so it claimed a gate state.json did not.
C  readability-v2 / radon-mi ran as `python -m harness.toolchains.X` from the
   project root; in a consumer project `harness` is the submodule root, so the
   tool found nothing and S4 reported the agent's score as fabricated.
D  the workflow's CRG-ARCH step relied on bash word-splitting of $BASELINE;
   the agent's shell is zsh, which passes "--baseline X" as one argument.
A  the Gate 4 orchestrator was told to dispatch Devil's Advocate challengers
   with an Agent tool a workflow agent does not have (generated-JS half here;
   the control-flow half is in js_src/sim_runner.test.mjs).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.gate_cmds import _cmd_finalize_gate_impl  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".claude" / "workflows"


# ── B / B2 ──────────────────────────────────────────────────────────────────

def _gate4_project(tmp_path: Path) -> Path:
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 98.0}), encoding="utf-8")
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"current_phase": 6, "last_gate": 3}), encoding="utf-8")
    (meth / "quality_manifest.json").write_text(
        json.dumps({"gate_results": {}}), encoding="utf-8")
    ver = tmp_path / "05-verification"
    ver.mkdir()
    for name in ("BASELINE.md", "VERIFICATION_REPORT.md"):
        (ver / name).write_text(f"# {name}\n", encoding="utf-8")
    return tmp_path


def _finalize(tmp_path: Path, monkeypatch, *, generator=None, drift_ok=True, gate=4, phase=6):
    """Run finalize-gate with the REAL artifact post-flight and the REAL Gate 4
    report generators; the checks that need a full project are stubbed with the
    same seam TestFinalizeGate4StateJsonWriteBeforePush uses. `generator`
    replaces the report module's entry point (public `load_harness_script`).
    Returns (rc, events)."""
    from harness.harness_bridge import GateResult

    events: list[str] = []
    monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda *_a: None)
    monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda *_a: None)
    monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda *_a: None)
    monkeypatch.setattr("cli.gate_cmds._check_gate4_prerequisites", lambda *_a: False)
    monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
    monkeypatch.setattr("core.quality_gate.crg_baseline.snapshot_baseline", lambda *_a: None)

    if generator is not None:
        import cli.gate_cmds as gc
        from core.utils.script_loader import load_harness_script as _real_load

        def _load(script):
            if script != "generate_quality_report.py":
                return _real_load(script)
            return type("M", (), {"generate_quality_report": staticmethod(generator)})

        monkeypatch.setattr(gc, "load_harness_script", _load)

    from core.phase_hooks import PhaseHooks
    _orig_links = PhaseHooks.postflight_artifact_links
    report = tmp_path / "06-quality" / "QUALITY_REPORT.md"

    def _links(self):
        events.append("postflight(report present)" if report.exists() else "postflight(report absent)")
        events.append(f"postflight(last_gate={_state(tmp_path).get('last_gate')})")
        return _orig_links(self)

    monkeypatch.setattr(PhaseHooks, "postflight_artifact_links", _links)
    monkeypatch.setattr(PhaseHooks, "postflight_drift_check",
                        lambda self: {"passed": drift_ok})

    class _Truth:
        def __init__(self, *_a, **_kw): pass
        def verify(self): return {"passed": True, "total_score": 100.0}
    import core.quality_gate.phase_truth_verifier as _ptv
    monkeypatch.setattr(_ptv, "PhaseTruthVerifier", _Truth)

    class _Git:
        def ensure_gitignore(self): pass
        def commit_fr_gate1(self, *_a): return True
        def commit_and_push_gate(self, *_a):
            events.append("commit")
            return True
    monkeypatch.setattr("cli._shared._make_git", lambda *_a: _Git())

    class _Bridge:
        def prepare_gate(self, **_): return object()
        def finalize_gate(self, _ctx, **_):
            return GateResult(gate_num=gate, score=98.0, dimensions=[],
                              open_critical=0, open_high=0,
                              quality_complete=True, rounds_used=1)
    import harness.harness_bridge as hb
    monkeypatch.setattr(hb, "HarnessBridge", _Bridge)

    rc = _cmd_finalize_gate_impl(argparse.Namespace(
        project=str(tmp_path), gate=gate, phase=phase, fr_id=None))
    return rc, events


def _state(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".methodology" / "state.json").read_text(encoding="utf-8"))


def _qc(tmp_path: Path, gate: int = 4):
    m = json.loads((tmp_path / ".methodology" / "quality_manifest.json").read_text(encoding="utf-8"))
    return (m["gate_results"].get(f"gate{gate}") or {}).get("quality_complete")


def test_first_gate4_renders_its_report_before_the_post_flight_that_requires_it(tmp_path, monkeypatch):
    project = _gate4_project(tmp_path)
    assert not (project / "06-quality" / "QUALITY_REPORT.md").exists()
    rc, events = _finalize(project, monkeypatch)
    assert rc == 0, events
    # rendered before post-flight, and before the gate was recorded as passed
    assert events[:2] == ["postflight(report present)", "postflight(last_gate=3)"], events
    assert "commit" in events
    assert _state(project)["last_gate"] == 4
    assert _qc(project) is True
    assert (project / "RELEASE_NOTES.md").is_file()


def test_a_generator_crash_leaves_no_gate_behind(tmp_path, monkeypatch):
    from cli.exit_codes import EX_HARNESS_BUG
    project = _gate4_project(tmp_path)

    def _crash(_project):
        raise RuntimeError("generator bug")

    rc, events = _finalize(project, monkeypatch, generator=_crash)
    assert rc == EX_HARNESS_BUG
    assert events == [], "nothing after the generator may run"
    assert _state(project)["last_gate"] == 3
    assert _qc(project) is False


def test_a_generator_that_writes_nothing_does_not_pass(tmp_path, monkeypatch):
    project = _gate4_project(tmp_path)
    rc, events = _finalize(project, monkeypatch, generator=lambda _p: None)
    assert rc != 0
    assert "commit" not in events
    assert _state(project)["last_gate"] == 3
    assert _qc(project) is False


def test_a_post_flight_block_rolls_the_manifest_back(tmp_path, monkeypatch):
    project = _gate4_project(tmp_path)
    rc, events = _finalize(project, monkeypatch, drift_ok=False)
    assert rc == 5
    assert "commit" not in events
    assert _state(project)["last_gate"] == 3
    assert _qc(project) is False, "quality_complete=True must imply the gate was finalized"


def test_a_rerun_rerenders_the_report(tmp_path, monkeypatch):
    project = _gate4_project(tmp_path)
    q = project / "06-quality"
    q.mkdir()
    (q / "QUALITY_REPORT.md").write_text("# stale\n", encoding="utf-8")
    rc, _events = _finalize(project, monkeypatch)
    assert rc == 0
    assert "# stale" not in (q / "QUALITY_REPORT.md").read_text(encoding="utf-8")


def test_gate3_never_renders_gate4_deliverables(tmp_path, monkeypatch):
    project = _gate4_project(tmp_path)
    (project / ".sessi-work" / "gate3_result.json").write_text(
        json.dumps({"composite_score": 90.0}), encoding="utf-8")
    _rc, _events = _finalize(project, monkeypatch, gate=3, phase=4)
    assert not (project / "06-quality" / "QUALITY_REPORT.md").exists()
    assert not (project / "RELEASE_NOTES.md").exists()


# ── C ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["readability-v2", "radon-mi"])
def test_harness_owned_scorers_run_from_a_consumer_project(tmp_path, tool):
    """A consumer vendors the harness at <project>/harness/, where `harness`
    is the submodule root and has no `toolchains` package."""
    from harness.toolchains.registry import get_tool_spec
    spec = get_tool_spec(tool)
    assert "-m" not in spec.cmd, f"{tool} must not be resolved via `-m harness...` from the project cwd"
    script = Path(spec.cmd[1])
    assert script.is_file() and script.parent == REPO / "harness" / "toolchains"

    (tmp_path / "harness").mkdir()  # submodule-shaped dir, no toolchains/
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "mod.py").write_text("def add(a, b):\n    return a + b\n")
    import shutil
    if shutil.which("radon") is None:
        pytest.skip("radon not installed")
    from harness.tool_runners import run_tool
    out, rc = run_tool(tool, str(tmp_path))
    assert rc == 0, out
    assert "No module named" not in out


# ── D ───────────────────────────────────────────────────────────────────────

def _arch_check(tmp_path, monkeypatch, *, baseline_file: bool, baseline_arg=None):
    from cli.checks import gates
    seen: dict = {}
    metrics = {"architecture_score": 100.0}
    monkeypatch.setattr("harness.crg_independent.run_independent_crg", lambda *_a: metrics)

    def _drift(bl, _m):
        seen["baseline"] = bl
        return {"drift": 0.0, "weight_covered": 1.0, "weight_total": 1.0, "absent": []}

    monkeypatch.setattr("harness.ssi.scripts.crg_analysis.structural_drift", _drift)
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(json.dumps({"current_phase": 6}), encoding="utf-8")
    if baseline_file:
        (meth / "crg_baseline_p4.json").write_text(json.dumps({"tag": "p4"}), encoding="utf-8")
    rc = gates.cmd_crg_arch_check(argparse.Namespace(
        project=str(tmp_path), threshold=None, baseline=baseline_arg, drift_threshold=0.4))
    return rc, seen


def test_crg_arch_check_applies_the_p4_baseline_without_being_told(tmp_path, monkeypatch):
    rc, seen = _arch_check(tmp_path, monkeypatch, baseline_file=True)
    assert rc == 0
    assert seen.get("baseline") == {"tag": "p4"}


def test_crg_arch_check_without_a_baseline_file_skips_drift(tmp_path, monkeypatch):
    rc, seen = _arch_check(tmp_path, monkeypatch, baseline_file=False)
    assert rc == 0
    assert "baseline" not in seen


def test_an_explicit_baseline_still_wins(tmp_path, monkeypatch):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"tag": "explicit"}), encoding="utf-8")
    rc, seen = _arch_check(tmp_path, monkeypatch, baseline_file=True, baseline_arg=str(other))
    assert rc == 0
    assert seen.get("baseline") == {"tag": "explicit"}


@pytest.mark.parametrize("name", [
    "phase3-implementation.js", "phase4-testing.js", "phase6-quality.js", "run-all.js"])
def test_no_workflow_command_depends_on_shell_word_splitting(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "$BASELINE" not in text, (
        f"{name}: the agent's Bash tool runs zsh, which does not word-split "
        f"$BASELINE — crg-arch-check resolves its baseline itself")


# ── A (generated JS; control flow is in sim_runner.test.mjs) ─────────────────

@pytest.mark.parametrize("name", ["phase6-quality.js", "run-all.js"])
def test_gate4_challengers_are_dispatched_by_the_workflow(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    g4 = text[text.index("YOU ARE THE GATE-4 ORCHESTRATOR") - 4000:]
    assert "you have the Agent tool" not in g4
    assert re.search(r"await parallel\(DA_DIMS\.map", text)
    assert "'gate4-da-' + dim + '-r' + round" in text
    assert "schema: DA_CHALLENGE_SCHEMA" in text
    assert "JSON.stringify(daChallenges)" in text


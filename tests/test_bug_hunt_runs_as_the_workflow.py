"""The P4 adversarial bug hunt is carried out by the workflow, on real targets.

Measured on taskq-open (P4, wf_bf1d7a87-a5b): the Bug Hunt step was ONE agent
told to "spawn hunters/verifiers as sub-agents (you have the Agent tool)". A
workflow agent has no Agent tool; it ran 0 sub-agents, hunted and verified its
own findings alone, and reported pass. The manifest it was handed also listed
4 high-risk "modules" that are not files and one file twice.

F1  bug-hunt-targets names every target as one project-relative existing file.
F1b record-bug-hunt writes the report from workflow-judged findings, in parts.
F2  the generated P4 script dispatches scout, hunters and verifiers itself
    (control flow: scripts/workflowgen/js_src/sim_runner.test.mjs).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first before cli imports
from cli.checks.hunt import _target_resolver, cmd_bug_hunt_targets, cmd_record_bug_hunt  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".claude" / "workflows"


# ── F1 ──────────────────────────────────────────────────────────────────────

def _project(tmp_path: Path) -> Path:
    src = tmp_path / "03-development" / "src" / "app"
    (src / "service").mkdir(parents=True)
    for rel in ("__init__.py", "service/__init__.py", "service/auth.py",
                "service/executor.py", "service/health.py", "cli.py"):
        (src / rel).write_text("x = 1\n", encoding="utf-8")
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(json.dumps({"current_phase": 4, "language": "python"}))
    return tmp_path


@pytest.mark.parametrize("raw, want", [
    ("app.service.auth", "03-development/src/app/service/auth.py"),
    ("03-development/src/app/cli.py", "03-development/src/app/cli.py"),
    ("/elsewhere/workdir/03-development/src/app/cli.py", "03-development/src/app/cli.py"),
    ("/elsewhere/nowhere/ghost.py", None),
    ("/elsewhere/cli.py", None),          # a bare filename names no file in particular
    ("app.no_such_module", None),
    ("03-development/src/app/missing.py", None),
])
def test_every_spelling_resolves_to_one_project_file_or_is_reported(tmp_path, raw, want):
    project = _project(tmp_path)
    target, _owner, unresolved = _target_resolver(project.resolve())
    assert target(raw) == want
    assert (raw in unresolved) is (want is None)


def test_an_absolute_path_inside_the_project_becomes_relative(tmp_path):
    project = _project(tmp_path).resolve()
    target, _owner, _unresolved = _target_resolver(project)
    assert target(str(project / "03-development/src/app/cli.py")) == "03-development/src/app/cli.py"


def test_the_manifest_lists_each_file_once_and_only_files(tmp_path):
    project = _project(tmp_path).resolve()
    (project / ".methodology" / "quality_manifest.json").write_text(json.dumps(
        {"high_risk_modules": ["app.service.auth", "app.service.executor"]}))
    survivors = [{"file": str(project / "03-development/src/app/service/executor.py")}] * 3 + [
        {"file": "/tmp/mutmut-workdir/03-development/src/app/service/health.py"}]
    (project / ".methodology" / "mutation_survivors.json").write_text(json.dumps({"survivors": survivors}))

    assert cmd_bug_hunt_targets(argparse.Namespace(project=str(project))) == 0
    t = json.loads((project / ".methodology" / "bug_hunt_targets.json").read_text())
    paths = [h["path"] for h in t["high_risk"]] + [s["path"] for s in t["standard"]]
    assert len(paths) == len(set(paths)), "a file is listed twice"
    assert all((project / p).is_file() and not p.startswith("/") for p in paths)
    executor = next(h for h in t["high_risk"] if h["path"].endswith("executor.py"))
    assert executor["reasons"] == ["declared", "mutation_survivors:3"]
    health = next(s for s in t["standard"] if s["path"].endswith("health.py"))
    assert health.get("survivors") == 1, "a workdir survivor path must annotate the project file"
    assert t["sources"]["unresolved"] == []


def test_threat_rows_carry_the_declared_mitigation_and_the_file(tmp_path, monkeypatch):
    project = _project(tmp_path).resolve()
    threat = {"id": "T-01", "category": "spoofing", "description": "forged key",
              "owner_module": "app.service.auth", "boundary": "TB-01",
              "mitigation": "hmac compare_digest", "verified_by": "test_sec_t01"}
    monkeypatch.setattr("core.quality_gate.security_design.extract_security_block",
                        lambda _p: {"security_design": {"applicability": "full", "threats": [threat]}})
    assert cmd_bug_hunt_targets(argparse.Namespace(project=str(project))) == 0
    row = json.loads((project / ".methodology" / "bug_hunt_targets.json").read_text())["threat_model"][0]
    assert row["mitigation"] == "hmac compare_digest"
    assert row["verified_by"] == "test_sec_t01"
    assert row["path"] == "03-development/src/app/service/auth.py"


# ── F1b ─────────────────────────────────────────────────────────────────────

def _finding(fid: str, *, confirmed: bool, severity: str = "medium", **extra) -> dict:
    row = {"id": fid, "module": fid.split("#")[0], "lens": "correctness", "severity": severity,
           "title": "t", "file": "03-development/src/app/cli.py", "line_start": 1,
           "reasoning": "cli.py:1", "confidence": "high", "confirmed": confirmed,
           "verify_evidence": "cli.py:1" if confirmed else "",
           "resolution": {"status": "open"} if confirmed else
           {"status": "refuted", "refute_evidence": "guard at cli.py:1"}}
    row.update(extra)
    return row


def _write_part(project: Path, k: int, findings: list) -> None:
    d = project / ".sessi-work" / "bug_hunt"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"part-{k}.json").write_text(json.dumps({"findings": findings}), encoding="utf-8")


def _record(project: Path, **kw) -> int:
    ns = {"project": str(project), "part": None, "assemble": None, "lenses": ""}
    ns.update(kw)
    return cmd_record_bug_hunt(argparse.Namespace(**ns))


def _git(project: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)


def test_parts_echo_what_they_hold_and_assemble_into_a_report_gate3_accepts(tmp_path, capsys):
    project = _project(tmp_path)
    _git(project)
    threat_row = _finding("T-01#1", confirmed=False, lens="threat-model", attack_vector="forged key",
                          attempted_exploit="guess", mitigation_effective=True, evidence="auth.py:1")
    _write_part(project, 1, [threat_row])
    _write_part(project, 2, [_finding("cli#1", confirmed=True), _finding("cli#2", confirmed=False)])

    assert _record(project, part=2) == 0
    assert "PART 2 findings=2 confirmed=1 first=cli#1 last=cli#2" in capsys.readouterr().out
    assert _record(project, assemble=2, lenses="threat-model,correctness") == 0
    assert "RECORDED findings=3 confirmed=1" in capsys.readouterr().out

    report = json.loads((project / ".methodology" / "bug_hunt_report.json").read_text())
    assert [f["id"] for f in report["findings"]] == ["T-01#1", "cli#1", "cli#2"]
    assert (report["raw_count"], report["confirmed_count"], report["refuted_count"]) == (3, 1, 2)
    assert report["lenses"] == ["threat-model", "correctness"]
    from core.quality_gate.bug_hunt_verifier import verify_bug_hunt_report
    assert verify_bug_hunt_report(str(project)).ok


def test_a_part_missing_required_fields_is_refused_at_the_part(tmp_path, capsys):
    project = _project(tmp_path)
    _write_part(project, 1, [{"id": "bad#1"}])
    assert _record(project, part=1) == 1
    assert "bad#1 is missing" in capsys.readouterr().out


def test_assemble_refuses_a_missing_part(tmp_path):
    project = _project(tmp_path)
    _git(project)
    _write_part(project, 1, [_finding("cli#1", confirmed=False)])
    assert _record(project, assemble=2) == 1
    assert not (project / ".methodology" / "bug_hunt_report.json").exists()


def test_a_malformed_hunt_leaves_the_previous_report_untouched(tmp_path):
    project = _project(tmp_path)
    _git(project)
    _write_part(project, 1, [_finding("cli#1", confirmed=False)])
    assert _record(project, assemble=1) == 0
    report = project / ".methodology" / "bug_hunt_report.json"
    before = report.read_bytes()
    _write_part(project, 1, [_finding("cli#1", confirmed=True, resolution={"status": "resolved"})])
    assert _record(project, assemble=1) == 1
    assert report.read_bytes() == before
    assert (project / ".sessi-work" / "bug_hunt_report.rejected.json").is_file()


def test_a_hunt_with_no_findings_is_a_valid_empty_report(tmp_path):
    project = _project(tmp_path)
    _git(project)
    assert _record(project, assemble=0) == 0
    report = json.loads((project / ".methodology" / "bug_hunt_report.json").read_text())
    assert report["findings"] == [] and report["raw_count"] == 0


# ── F2 (generated JS; control flow in sim_runner.test.mjs) ───────────────────

@pytest.mark.parametrize("name", ["phase4-testing.js", "run-all.js"])
def test_the_hunt_is_dispatched_by_the_workflow(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "you have the Agent tool" not in text
    assert "await pipeline(huntPairs" in text
    assert "'hunt-' + i + '-' + j + '-' + role" in text
    assert "record-bug-hunt --project ' + REPO + ' --part '" in text
    assert "record-bug-hunt --project ' + REPO + ' --assemble '" in text

"""Black-box golden-path journeys — real CLI, real git repos (弱點強化 C3).

Complements tests/test_e2e_cli.py (run-phase pass/block journeys live
there). Each journey here pins an end-to-end contract of the P0 split-brain
work at the command boundary: what the operator actually runs and sees.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HARNESS_CLI = Path(__file__).resolve().parents[2] / "harness_cli.py"


def run_cli(args: list[str], project: "Path | None" = None) -> subprocess.CompletedProcess:
    """Run `python harness_cli.py <args> [--project <project>]` for real.

    project=None for subcommands that take no --project flag
    (--help, print-legal-artifacts)."""
    cmd = [sys.executable, str(HARNESS_CLI), *args]
    if project is not None:
        cmd += ["--project", str(project)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


class TestDoctorJourneys:
    def test_clean_project_reports_ok_exit_0(self, e2e_project):
        r = run_cli(["doctor"], e2e_project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "OK" in r.stdout

    def test_ghost_state_detected_exit_1(self, e2e_project):
        """B2 journey: state.json hand-bumped past git's advance record —
        doctor must call out the split-brain and exit non-zero."""
        state = e2e_project / ".methodology" / "state.json"
        state.write_text(
            json.dumps({"state": "RUNNING", "current_phase": 3}) + "\n",
            encoding="utf-8",
        )
        git(e2e_project, "commit", "--allow-empty", "-m",
            "handover: advance to Phase 2")

        r = run_cli(["doctor"], e2e_project)
        assert r.returncode == 1
        assert "ghost state" in r.stdout
        assert "git-sync" in r.stdout


class TestAdvanceJourneys:
    def test_healthy_advance_commits_and_bumps_state(self, e2e_project):
        r = run_cli(["advance-phase", "--completed", "1"], e2e_project)
        assert r.returncode == 0, r.stdout + r.stderr

        state = json.loads(
            (e2e_project / ".methodology" / "state.json").read_text()
        )
        assert state["current_phase"] == 2
        subject = git(e2e_project, "log", "-1", "--format=%s").stdout.strip()
        assert subject == "handover: advance to Phase 2"
        committed = git(
            e2e_project, "show", "HEAD:.methodology/state.json"
        ).stdout
        assert json.loads(committed)["current_phase"] == 2
        # and doctor agrees the world is consistent afterwards
        assert run_cli(["doctor"], e2e_project).returncode == 0

    def test_rejected_commit_rolls_back_exit_6(self, e2e_project):
        """B1 journey: hook rejects the handover commit → exit 6, state
        restored, project re-runnable after fixing the hook."""
        hook = e2e_project / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'rejected by hook' >&2\nexit 1\n")
        hook.chmod(0o755)

        r = run_cli(["advance-phase", "--completed", "1"], e2e_project)
        assert r.returncode == 6, r.stdout + r.stderr
        assert "BLOCKED" in r.stderr
        state = json.loads(
            (e2e_project / ".methodology" / "state.json").read_text()
        )
        assert state["current_phase"] == 1, "ghost state at the CLI boundary"
        assert not (e2e_project / "HANDOVER.md").exists()

        hook.unlink()
        r2 = run_cli(["advance-phase", "--completed", "1"], e2e_project)
        assert r2.returncode == 0, r2.stdout + r2.stderr


class TestFastPathJourneys:
    def test_pre_commit_check_passes_on_clean_phase1(self, e2e_project):
        """The git-hook fast path: fresh attestation (built by the fixture via
        the real build-trace-attestation CLI) + healthy FSM → exit 0."""
        r = run_cli(["pre-commit-check", "--phase", "1"], e2e_project)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_push_milestone_dry_run_makes_no_commit(self, e2e_project):
        head_before = git(e2e_project, "rev-parse", "HEAD").stdout.strip()
        r = run_cli(
            ["push-milestone", "--type", "p3-mid", "--fr-done", "1",
             "--fr-total", "2", "--dry-run"],
            e2e_project,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "dry-run" in r.stdout
        assert git(e2e_project, "rev-parse", "HEAD").stdout.strip() == head_before

    def test_help_smoke(self):
        r = run_cli(["--help"])
        assert r.returncode == 0
        assert "advance-phase" in r.stdout

    def test_print_legal_artifacts_smoke(self):
        r = run_cli(["print-legal-artifacts"])
        assert r.returncode == 0, r.stdout + r.stderr
        payload = json.loads(r.stdout)
        assert "legal_artifacts" in payload

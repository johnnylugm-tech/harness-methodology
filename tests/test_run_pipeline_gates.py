# test_run_pipeline_gates.py
# Tests for cmd_run_pipeline P1/P2 inline gates (checklist + Agent B approvals)
# Finding: cmd_run_pipeline P1/P2 gates (lines 2909-2922, 2960-2973) untested
"""
Test cases for cmd_run_pipeline P1/P2 gate logic:
  - P1 mandatory checklist unchecked → exit 5
  - P1 Agent B approval missing → exit 5
  - P2 mandatory checklist unchecked → exit 5
  - P2 Agent B approval missing → exit 5
  - P1/P2 all gates pass → continues

Exit codes:
  0  — phases completed
  5  — gate blocked (checklist or Agent B approvals)
  10 — PAUSE (missing SRS/SAD, etc.)
"""

import pytest
import json as _json
from pathlib import Path
from io import StringIO


class TestCmdRunPipelineGates:
    """Test cmd_run_pipeline P1/P2 inline gate logic.

    The new gates added in the f5aa317 commit check:
      1. _parse_plan_unchecked  — blocks if mandatory checklist items unchecked
      2. _verify_agent_b_approvals_core — blocks if Agent B approval JSON missing or non-APPROVE
    """

    @staticmethod
    def _make_args(phase_from=1, phase_to=1, project=None, **kwargs):
        """Build a minimal args Namespace for cmd_run_pipeline."""
        class _Args:
            pass
        a = _Args()
        a.phase_from = phase_from
        a.phase_to = phase_to
        a.project = str(project) if project else "/tmp/pipeline_test"
        a.no_kill_switch = True  # avoid KillSwitch import in tests
        a.drift_threshold = 85.0
        a.no_auto_fix = True
        a.auto_fix_rounds = 3
        for k, v in kwargs.items():
            setattr(a, k, v)
        return a

    @staticmethod
    def _call_run_pipeline(tmp_path, monkeypatch, phase_from=1, phase_to=1,
                           checklist_items=None, agent_b_approvals=None,
                           srs_exists=True, sad_exists=False,
                           skip_git_commit=True):
        """Call cmd_run_pipeline in a controlled environment.

        checklist_items: list of unchecked mandatory item labels (e.g. ["A-2", "B-2"])
                         If None, no phase plan is created (plan existence is optional).
        agent_b_approvals: dict of {deliverable_id: {"review_status": "APPROVE"|"REJECT"|None}}
                          None = don't create approvals dir at all (missing).
        srs_exists: create 01-requirements/SRS.md if True
        sad_exists: create 02-architecture/SAD.md if True (for P2)
        skip_git_commit: monkeypatch git operations to no-op
        """
        from harness_cli import cmd_run_pipeline
        project = tmp_path

        # ── Directory structure ────────────────────────────────────────────
        method_dir = project / ".methodology"
        method_dir.mkdir(parents=True, exist_ok=True)

        # sessions_spawn.log must exist for P1 checkpoint
        sessions_log = method_dir / "sessions_spawn.log"
        sessions_log.write_text(_json.dumps({"phase": 1, "agent": "B"}) + "\n")

        if srs_exists:
            srs_dir = project / "01-requirements"
            srs_dir.mkdir(parents=True, exist_ok=True)
            (srs_dir / "SRS.md").write_text("## FR-01: Foo\nFeature foo.\n")

        if sad_exists:
            sad_dir = project / "02-architecture"
            sad_dir.mkdir(parents=True, exist_ok=True)
            (sad_dir / "SAD.md").write_text("## FR-01: Foo\nArchitecture.\n")

        # ── Phase plan (checklist gate) ────────────────────────────────────
        if checklist_items is not None:
            plan_file = method_dir / f"phase{phase_from}_plan.md"
            lines = []
            for item in checklist_items:
                lines.append(f"- [ ] **{item}** some description")
            plan_file.write_text("\n".join(lines))

        # ── Agent B approvals ──────────────────────────────────────────────
        if agent_b_approvals is not None:
            approvals_dir = method_dir / "agent_b_approvals"
            approvals_dir.mkdir(parents=True, exist_ok=True)
            for did, data in agent_b_approvals.items():
                if data is not None:
                    approvals_dir / f"{did}.json"
                    (approvals_dir / f"{did}.json").write_text(
                        _json.dumps(data)
                    )

        # ── Monkeypatch: suppress git operations ───────────────────────────
        if skip_git_commit:
            class _FakeRunResult:
                returncode = 0
                stdout = ""
                stderr = ""
            def _fake_run(cmd, **kw):
                return _FakeRunResult()
            monkeypatch.setattr("subprocess.run", _fake_run)
            monkeypatch.setattr(
                "harness.handover_generator.HandoverGenerator.write",
                lambda self, **kw: project / "HANDOVER.md"
            )
            # Mock _generate_sab_json to return True
            monkeypatch.setattr(
                "harness_cli._generate_sab_json",
                lambda project: True
            )
            # Mock _advance_fsm to no-op
            monkeypatch.setattr(
                "harness_cli._advance_fsm",
                lambda project, phase, **kw: None
            )

        captured = StringIO()
        monkeypatch.setattr("sys.stdout", captured)

        args = TestCmdRunPipelineGates._make_args(
            phase_from=phase_from, phase_to=phase_to, project=str(project)
        )
        try:
            exit_code = cmd_run_pipeline(args)
        except SystemExit as e:
            exit_code = e.code

        return exit_code, captured.getvalue()

    # ── P1 Gates ────────────────────────────────────────────────────────────

    def test_p1_checklist_unchecked_blocks(self, tmp_path, monkeypatch):
        """P1: unchecked mandatory items in phase1_plan.md → exit 5."""
        # A-2 and B-2 are in _MANDATORY_CHECKLIST_LABELS
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=1, phase_to=1,
            checklist_items=["A-2", "B-2"],
            agent_b_approvals={"SRS.md": {"review_status": "APPROVE",
                                           "docs_embedded": ["SRS.md"]}},
        )
        assert exit_code == 5, f"Expected exit 5, got {exit_code}. Output:\n{output}"
        assert "BLOCKED" in output or "mandatory" in output.lower()

    def test_p1_agent_b_approval_missing_blocks(self, tmp_path, monkeypatch):
        """P1: no agent_b_approvals/SRS.md.json → exit 5."""
        # No agent_b_approvals dict = approvals dir not created
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=1, phase_to=1,
            checklist_items=[],  # all checked
            agent_b_approvals=None,  # means don't create any approvals
        )
        assert exit_code == 5, f"Expected exit 5, got {exit_code}. Output:\n{output}"
        assert "BLOCKED" in output or "missing" in output.lower()

    def test_p1_agent_b_approval_reject_blocks(self, tmp_path, monkeypatch):
        """P1: agent_b_approvals/SRS.md.json with review_status=REJECT → exit 5."""
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=1, phase_to=1,
            checklist_items=[],
            agent_b_approvals={
                "SRS.md": {"review_status": "REJECT", "docs_embedded": ["SRS.md"]},
            },
        )
        assert exit_code == 5, f"Expected exit 5, got {exit_code}. Output:\n{output}"
        assert "BLOCKED" in output or "REJECT" in output

    def test_p1_all_gates_pass_continues(self, tmp_path, monkeypatch):
        """P1: checklist all checked + all Agent B APPROVE → proceeds (exit 0 or 10 for next phase)."""
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=1, phase_to=1,
            checklist_items=[],  # all checked — no mandatory unchecked
            agent_b_approvals={
                "SRS.md":       {"review_status": "APPROVE", "docs_embedded": ["SRS.md"]},
                "SPEC_TRACKING.md": {"review_status": "APPROVE", "docs_embedded": ["SRS.md"]},
                "TRACEABILITY_MATRIX.md": {"review_status": "APPROVE", "docs_embedded": ["SRS.md"]},
            },
        )
        # exit 0 means pipeline completed; since phase_to=1, it should exit 0
        assert exit_code == 0, f"Expected exit 0, got {exit_code}. Output:\n{output}"
        assert "[1.1]" in output  # P1 checkpoint was reached

    # ── P2 Gates ────────────────────────────────────────────────────────────

    def test_p2_checklist_unchecked_blocks(self, tmp_path, monkeypatch):
        """P2: unchecked mandatory items in phase2_plan.md → exit 5."""
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=2, phase_to=2,
            checklist_items=["B-READ", "B-DECIDE"],
            agent_b_approvals={
                "SAD.md": {"review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"]},
                "ADR.md":  {"review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"]},
            },
            srs_exists=True,
            sad_exists=True,
        )
        assert exit_code == 5, f"Expected exit 5, got {exit_code}. Output:\n{output}"
        assert "BLOCKED" in output or "mandatory" in output.lower()

    def test_p2_agent_b_approval_missing_blocks(self, tmp_path, monkeypatch):
        """P2: no agent_b_approvals/SAD.md.json → exit 5."""
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=2, phase_to=2,
            checklist_items=[],
            agent_b_approvals=None,
            srs_exists=True,
            sad_exists=True,
        )
        assert exit_code == 5, f"Expected exit 5, got {exit_code}. Output:\n{output}"

    def test_p2_all_gates_pass_continues(self, tmp_path, monkeypatch):
        """P2: checklist all checked + all Agent B APPROVE → proceeds (exit 0)."""
        exit_code, output = self._call_run_pipeline(
            tmp_path, monkeypatch,
            phase_from=2, phase_to=2,
            checklist_items=[],
            agent_b_approvals={
                "SAD.md": {"review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"]},
                "ADR.md":  {"review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"]},
            },
            srs_exists=True,
            sad_exists=True,
        )
        assert exit_code == 0, f"Expected exit 0, got {exit_code}. Output:\n{output}"
        assert "[2.2]" in output  # P2 checkpoint reached
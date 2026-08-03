"""
Regression tests for 2 HIGH path-traversal bugs in harness_bridge.py:

  1. _check_tool_evidence (line 663) — tool_output joined to
     ctx.project_root with no containment check, allowing agent-written
     `../../etc/passwd` / absolute paths / symlink escapes to be read
     by the audit cross-check.

  2. _run_harness_cross_validation (line 937) — same pattern for
     skip-list tool (mutmut/scancode) tool_output files.

Contract under test:
  - A tool_output value that resolves to a path OUTSIDE ctx.project_root
    must NOT be read by the harness, and must surface as a violation
    (so the gate stays blocked when the agent's evidence is untrusted).
  - A tool_output value that resolves to a path INSIDE ctx.project_root
    must still be read normally (regression guard).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import core.quality_gate.gate_thresholds as _gt

import pytest

from harness.harness_bridge import (
    GateContext,
    _check_tool_evidence,
    _run_harness_cross_validation,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def project_with_gate_config(tmp_path: Path) -> Path:
    """
    Create a project skeleton with a gate3 config so _check_tool_evidence
    can find a `gate3_*.yaml` to load.  Uses gate_config_path() resolver;
    callers must monkeypatch `core.quality_gate.gate_thresholds.gate_config_path`
    to return this config.
    """
    cfg_path = tmp_path / "gate3_p4_exit.yaml"
    cfg_path.write_text(
        "gate_num: 3\n"
        "score_gate: 75.0\n"
        "dimensions:\n"
        "  - { name: linting,  tier: 1, threshold: 90, weight: 0.10,\n"
        "      tool: ruff,  requires_tool_execution: true }\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def ctx(project_with_gate_config: Path) -> GateContext:
    return GateContext(
        gate_num=3,
        config={},
        project_root=str(project_with_gate_config),
        phase=4,
        fr_id="FR-001",
        ssi_scripts_dir="/dev/null",
        ssi_prompts_dir="/dev/null",
        ssi_schemas_dir="/dev/null",
        work_dir=str(project_with_gate_config / "work"),
    )


# ── _check_tool_evidence ─────────────────────────────────────────────────────

class TestCheckToolEvidencePathContainment:
    def test_parent_traversal_in_tool_output_is_rejected(
        self, ctx: GateContext, monkeypatch,
    ):
        """`tool_output: ../../etc/passwd` joined to project_root must
        not be read — the resolved path escapes project_root."""
        cfg_path = Path(ctx.project_root) / "gate3_p4_exit.yaml"
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
        raw = {
            "breakdown": {
                "linting": {
                    "tool_output": "../../../../etc/passwd",
                    "score": 95.0,
                },
            },
        }
        # If the bug is present, the function would raise (FileNotFoundError
        # for a non-existent file) or read `/etc/passwd` content. After the
        # fix, the containment check fires first and returns a violation
        # without ever touching the filesystem.
        violations = _check_tool_evidence(ctx, raw)
        assert any("escapes" in v or "outside" in v or "project" in v
                   for v in violations), (
            f"path-traversal tool_output must produce a containment "
            f"violation; got {violations!r}"
        )

    def test_absolute_path_tool_output_is_rejected(
        self, ctx: GateContext, monkeypatch,
    ):
        """An absolute tool_output (e.g. `/etc/passwd`) must be rejected
        the same way as a parent-traversal."""
        cfg_path = Path(ctx.project_root) / "gate3_p4_exit.yaml"
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
        raw = {
            "breakdown": {
                "linting": {
                    "tool_output": "/etc/passwd",
                    "score": 95.0,
                },
            },
        }
        violations = _check_tool_evidence(ctx, raw)
        assert any("escapes" in v or "outside" in v or "project" in v
                   for v in violations), (
            f"absolute-path tool_output must produce a containment "
            f"violation; got {violations!r}"
        )

    def test_symlink_escape_tool_output_is_rejected(
        self, ctx: GateContext, tmp_path: Path, monkeypatch,
    ):
        """A symlink inside project_root that points outside it must
        be rejected by the containment check (`resolve()` follows the
        symlink, so the resolved path is outside project_root)."""
        cfg_path = Path(ctx.project_root) / "gate3_p4_exit.yaml"
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
        # Create a real file outside the project
        outside_dir = tmp_path.parent / "outside_secret"
        outside_dir.mkdir(exist_ok=True)
        secret_file = outside_dir / "secret.txt"
        secret_file.write_text("SECRET DATA", encoding="utf-8")
        try:
            # Create a symlink inside project_root that points to the file
            project_root = Path(ctx.project_root)
            evidence_dir = project_root / "evidence"
            evidence_dir.mkdir(exist_ok=True)
            symlink_path = evidence_dir / "link_to_secret.txt"
            try:
                symlink_path.symlink_to(secret_file)
            except OSError:
                pytest.skip("symlink not supported in this environment")

            raw = {
                "breakdown": {
                    "linting": {
                        "tool_output": "evidence/link_to_secret.txt",
                        "score": 95.0,
                    },
                },
            }
            violations = _check_tool_evidence(ctx, raw)
            assert any("escapes" in v or "outside" in v or "project" in v
                       for v in violations), (
                f"symlink-escape tool_output must produce a containment "
                f"violation; got {violations!r}"
            )
        finally:
            # Cleanup
            secret_file.unlink(missing_ok=True)
            outside_dir.rmdir()

    def test_valid_tool_output_inside_project_still_works(
        self, ctx: GateContext, monkeypatch,
    ):
        """Sanity guard: a tool_output path that genuinely lives inside
        project_root must still be read normally (no false-positive
        containment rejection)."""
        cfg_path = Path(ctx.project_root) / "gate3_p4_exit.yaml"
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
        project_root = Path(ctx.project_root)
        evidence_dir = project_root / "evidence"
        evidence_dir.mkdir(exist_ok=True)
        # Write a file that matches ruff's "All checks passed" pattern
        evidence_file = evidence_dir / "ruff_output.txt"
        evidence_file.write_text("All checks passed!\n", encoding="utf-8")

        raw = {
            "breakdown": {
                "linting": {
                    "tool_output": "evidence/ruff_output.txt",
                    "score": 95.0,
                },
            },
        }
        violations = _check_tool_evidence(ctx, raw)
        # No containment violation should be raised. Other violations are
        # possible (e.g. if format validation fails) but the file is read.
        assert not any("escapes" in v or "outside" in v or "project" in v
                       for v in violations), (
            f"valid in-project tool_output must NOT trigger containment "
            f"violation; got {violations!r}"
        )


# ── _run_harness_cross_validation ────────────────────────────────────────────

class TestRunHarnessCrossValidationPathContainment:
    def test_parent_traversal_in_skip_list_tool_output_is_rejected(
        self, ctx: GateContext, monkeypatch,
    ):
        """The S4 cross-validation's skip-list branch (mutmut/scancode)
        must also reject tool_output paths that escape project_root."""
        # Configure a dimension with a skip-list tool (mutmut) and a
        # malicious tool_output. We need agent_score >= threshold so
        # the function actually reaches the path-traversal sink, and
        # we need run_tool to return (-1) to enter the skip-list branch.
        project_root = Path(ctx.project_root)
        # Override the gate config to use mutmut (a skip-list tool)
        cfg_path = project_root / "gate3_p4_exit.yaml"
        cfg_path.write_text(
            "gate_num: 3\n"
            "score_gate: 75.0\n"
            "dimensions:\n"
            "  - { name: mutation_score,  tier: 1, threshold: 70,\n"
            "      weight: 0.10, tool: mutmut,  requires_tool_execution: true }\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)

        # run_tool returns (-1) → skip-list branch
        with patch(
            "harness.tool_runners.run_tool", return_value=("", -1),
        ), patch(
            "harness.tool_runners.compute_tool_score", return_value=None,
        ):
            raw = {
                "breakdown": {
                    "mutation_score": {
                        "tool_output": "../../../../etc/passwd",
                        "score": 95.0,  # >= threshold 70 → cross-validation engages
                    },
                },
            }
            violations, _unver = _run_harness_cross_validation(ctx, raw)
        assert any("escapes" in v or "outside" in v or "project" in v
                   for v in violations), (
            f"path-traversal tool_output in skip-list branch must produce "
            f"a containment violation; got {violations!r}"
        )

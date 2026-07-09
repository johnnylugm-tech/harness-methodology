"""E2E CLI tests — subprocess-level validation of critical harness_cli paths.

These tests call the real CLI via subprocess, verifying exit codes and output
at the command boundary. They serve as regression guards for bugs that are only
visible at the integration layer (e.g. Bug 3: KeyError crash in _verify_entry_gate
when manifest has gate=None; auto-fix teardown: no [AUTO-FIX] on preflight block).
"""
import json
import subprocess
import sys
from pathlib import Path

HARNESS_CLI = Path(__file__).parent.parent / "harness_cli.py"


def _run(
    args: list[str],
    project: Path,
    env: "dict | None" = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HARNESS_CLI)] + args + ["--project", str(project)],
        capture_output=True,
        text=True,
        env=env,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# run-phase preflight failure paths
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunPhaseCLI:

    def test_fsm_freeze_blocks_with_exit_1_and_no_autofix(self, tmp_path):
        """FSM FREEZE → run-phase --phase 1 → exit 1 (preflight block, no auto-fix).

        P1 has no entry gate (always passes), so the FREEZE state is caught by
        preflight_fsm_check → exit 1.

        Regression: preflight auto-fix was removed (d673399) because strategies
        cannot clear substantive checks. No [AUTO-FIX] output must ever appear.
        """
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"state": "FREEZE", "current_phase": 1})
        )

        result = _run(["run-phase", "--phase", "1"], tmp_path)

        assert result.returncode == 1, (
            f"Expected exit 1 (preflight block), got {result.returncode}\n"
            f"stdout: {result.stdout[:500]}"
        )
        # auto-fix teardown regression guard
        assert "[AUTO-FIX]" not in result.stdout + result.stderr, (
            "Preflight auto-fix was removed — [AUTO-FIX] must not appear"
        )
        assert "PRE-FLIGHT" in result.stdout

    def test_entry_gate_none_no_crash(self, tmp_path):
        """Bug 3 regression: manifest gate=None → run-phase --phase 8 → exit 10.

        Before the fix (_verify_entry_gate L1215), None.get() raised AttributeError,
        caught by except which returned a dict missing 'gate' → KeyError crash at
        L1297 entry_gate['gate']. Now must cleanly return exit 10 (ENTRY GATE FAILED).
        """
        (tmp_path / ".methodology").mkdir()
        manifest = {
            "gate_results": {
                "gate1": {},
                "gate2": None,
                "gate3": None,
                "gate4": None,
            }
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(manifest)
        )

        result = _run(["run-phase", "--phase", "8"], tmp_path)

        assert result.returncode == 10, (
            f"Expected exit 10 (ENTRY GATE FAILED), got {result.returncode}\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )
        assert "ENTRY GATE FAILED" in result.stdout
        assert "Traceback" not in result.stderr, (
            "KeyError crash must not occur — Bug 3 regression"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# finalize-gate fail-to-pass: tool enforcement (S0, now tool_checks.verify_gate_tools)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunPhasePassCLI:

    def test_run_phase_p1_no_entry_gate_passes_preflight(self, tmp_path):
        """run-phase --phase 1 with clean state → preflight PASS (exit 0).

        P1 has no entry gate. With a healthy RUNNING state and empty project,
        all preflight checks either PASS or skip gracefully.
        This is the fail-to-pass scenario complement: after fixing a FREEZE (which
        blocks, verified in test_fsm_freeze_blocks_with_exit_1_and_no_autofix),
        restoring RUNNING state lets the phase proceed.

        fail  : state=FREEZE → exit 1 (covered by prior test)
        to pass: state=RUNNING → exit 0, PRE-FLIGHT: PASS in output
        """
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"state": "RUNNING", "current_phase": 1})
        )

        result = _run(["run-phase", "--phase", "1"], tmp_path)

        assert result.returncode == 0, (
            f"Expected exit 0 (preflight PASS), got {result.returncode}\n"
            f"stdout: {result.stdout[:600]}"
        )
        assert "PRE-FLIGHT: PASS" in result.stdout

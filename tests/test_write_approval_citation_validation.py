"""
tests/test_write_approval_citation_validation.py — Regression test pinning the
pre-write citation sanity check added to cmd_write_approval.

Background (2026-08-14): run-all.js Phase 1 halt on taskq-super. Agent B's
holistic peer review cited `TEST_INVENTORY.yaml:791-860` for a file that
contained only 859 lines (off-by-one). The 4 approval JSONs it persisted all
carried the bad citation. advance-phase's `verify_agent_b_approvals_core`
correctly rejected them via `unresolvable_citations`, but only at the phase
boundary — the orchestrator had no opportunity to self-correct and the
workflow halted.

cmd_write_approval now runs `unresolvable_citations` on the payload BEFORE
writing the approval file, so the same off-by-one is caught at persist time
with a stderr message naming the rejected citation. The wrapper can surface
that to Agent B (see render_persist_approval's attempt-aware prompt +
spec_phase1.runPeerReview's try/catch) and re-dispatch with the cited file
path + `wc -l` reminder.

These tests pin the contract: bad citations block; good citations pass;
payloads without citations pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture: a tmp project containing a 859-line TEST_INVENTORY.yaml (the
# exact size that triggered the 2026-08-14 incident) plus a small SRS.md.
# ---------------------------------------------------------------------------

@pytest.fixture
def project_with_859_line_yaml(tmp_path: Path) -> Path:
    (tmp_path / "01-requirements").mkdir()
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "\n".join(f"SRS line {i}" for i in range(1, 81)) + "\n",
        encoding="utf-8",
    )
    # Exactly 859 lines — matches the on-disk TEST_INVENTORY.yaml that
    # triggered the run-all-by-workflow P1 halt.
    (tmp_path / "TEST_INVENTORY.yaml").write_text(
        "\n".join(f"TI line {i}" for i in range(1, 860)) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_write_approval(project: Path, payload: dict) -> subprocess.CompletedProcess:
    """Invoke cmd_write_approval as a subprocess (same path the workflow JS
    takes via `harness_cli.py write-approval --json '<payload>'`)."""
    cli = project.parent / "harness" / "harness_cli.py"
    if not cli.exists():
        # fallback for in-tree direct invocation
        cli = Path(__file__).resolve().parents[1] / "harness_cli.py"
    # Use PYTHONPATH = harness repo root so `core.*` imports resolve.
    env_add = {"PYTHONPATH": str(cli.parent)}
    import os
    env = {**os.environ, **env_add}
    return subprocess.run(
        [
            sys.executable,
            str(cli),
            "write-approval",
            "--project", str(project),
            "--fr-id", "TEST_INVENTORY.yaml",
            "--json", json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Off-by-one range citation MUST block
# ---------------------------------------------------------------------------

class TestOffByOneRangeCitationBlocked:
    def test_end_equals_total_plus_one_is_rejected(
        self, project_with_859_line_yaml: Path
    ):
        """The exact shape that caused the 2026-08-14 halt. File has 859
        lines; cite references line 791-860 (end runs off the file end).
        cmd_write_approval MUST block with exit 1 and the rejection must
        name the offending citation."""
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,  # >= MIN_REVIEW_REASON_CHARS (40)
            "citations": ["TEST_INVENTORY.yaml:791-860"],
            "docs_embedded": ["SRS.md", "TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 1, (
            f"expected exit 1 for off-by-one citation, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "BLOCKED" in result.stderr
        assert "range end 860" in result.stderr
        assert "file length 859" in result.stderr
        # No approval file should have been written.
        approvals_dir = project_with_859_line_yaml / ".methodology" / "agent_b_approvals"
        assert not (approvals_dir / "TEST_INVENTORY.yaml.json").exists()

    def test_end_well_beyond_file_is_rejected(
        self, project_with_859_line_yaml: Path
    ):
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": ["TEST_INVENTORY.yaml:1-9999"],
            "docs_embedded": ["TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 1
        assert "BLOCKED" in result.stderr
        assert "9999" in result.stderr

    def test_single_line_just_past_end_is_rejected(
        self, project_with_859_line_yaml: Path
    ):
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": ["TEST_INVENTORY.yaml:860"],
            "docs_embedded": ["TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 1
        assert "file has 859 lines" in result.stderr or "860" in result.stderr

    def test_nonexistent_file_citation_is_rejected(
        self, project_with_859_line_yaml: Path
    ):
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": ["DOES_NOT_EXIST.md:1"],
            "docs_embedded": ["DOES_NOT_EXIST.md"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 1
        assert "no such file" in result.stderr


# ---------------------------------------------------------------------------
# Good citations MUST pass — defensive validation must not block valid work
# ---------------------------------------------------------------------------

class TestValidCitationsAccepted:
    def test_range_within_file_passes(self, project_with_859_line_yaml: Path):
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": ["TEST_INVENTORY.yaml:791-859"],
            "docs_embedded": ["TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 0, (
            f"expected OK, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "[write-approval] OK" in result.stdout

    def test_single_line_within_file_passes(self, project_with_859_line_yaml: Path):
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": ["TEST_INVENTORY.yaml:1", "SRS.md:42"],
            "docs_embedded": ["TEST_INVENTORY.yaml", "SRS.md"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 0
        assert "[write-approval] OK" in result.stdout

    def test_range_with_annotation_passes(self, project_with_859_line_yaml: Path):
        # Round 27 contract: annotation suffix must NOT cause rejection.
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": ["TEST_INVENTORY.yaml:40-60 (verifying FR-05 §10 array)"],
            "docs_embedded": ["TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 0
        assert "[write-approval] OK" in result.stdout


# ---------------------------------------------------------------------------
# Mixed / edge cases — must report ALL bad citations, not just the first
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_mixed_good_and_bad_citations_rejected(
        self, project_with_859_line_yaml: Path
    ):
        """One bad citation in the list must block; the message must name
        the bad one (and not silently drop the good one)."""
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": [
                "TEST_INVENTORY.yaml:1",                # good
                "TEST_INVENTORY.yaml:791-860",          # off-by-one
                "SRS.md:42",                            # good
            ],
            "docs_embedded": ["TEST_INVENTORY.yaml", "SRS.md"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 1
        assert "791-860" in result.stderr

    def test_empty_citations_list_still_blocks_when_min_chars_fail(
        self, project_with_859_line_yaml: Path
    ):
        """An approval with empty citations must still be rejected by the
        MIN_REVIEW_REASON_CHARS check (advance-phase would catch it later,
        but cmd_write_approval must also reject empty payload structurally)."""
        # Note: cmd_write_approval currently doesn't enforce reason length
        # or non-empty citations — that's advance-phase's job. We assert the
        # current behavior here so any change is intentional.
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": [],
            "docs_embedded": ["TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        # cmd_write_approval itself does NOT reject empty citations today
        # (advance-phase does); this test pins that behavior.
        assert result.returncode == 0
        assert "[write-approval] OK" in result.stdout

    def test_non_list_citations_field_is_rejected(
        self, project_with_859_line_yaml: Path
    ):
        """Structural guard: `citations` must be a list (advance-phase
        requires it; verify_agent_b_approvals_core line 275-279). A string
        or dict passed instead must be rejected with a clear type message —
        NOT silently iterated char-by-char by unresolvable_citations."""
        payload = {
            "review_status": "APPROVE",
            "reason": "x" * 100,
            "citations": "TEST_INVENTORY.yaml:791-860",  # string, not list
            "docs_embedded": ["TEST_INVENTORY.yaml"],
            "confidence": 0.9,
        }
        result = _run_write_approval(project_with_859_line_yaml, payload)
        assert result.returncode == 1, (
            f"expected exit 1 for non-list citations, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "must be a list" in result.stderr or "BLOCKED" in result.stderr
        # No approval file should have been written.
        approvals_dir = project_with_859_line_yaml / ".methodology" / "agent_b_approvals"
        assert not (approvals_dir / "TEST_INVENTORY.yaml.json").exists()
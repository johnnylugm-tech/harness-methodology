"""O6 carry-over (2026-07-07): phase4-testing.js added `phase('Artifacts Commit')`
in commit d4f4724. This test asserts the same pattern is present in phase5/7/8
workflow JS so each phase's Gate FAIL early-return doesn't leave deterministic
artifacts dirty on the working tree.

Pattern invariants:
  - The string `phase('Artifacts Commit')` exists exactly once
  - `meta.phases` array includes `{ title: 'Artifacts Commit' }`
  - The git command uses an explicit path allowlist (NOT `git add -A`)
  - Uses `|| true` for idempotency on the no-op case
"""

from __future__ import annotations

import re
from pathlib import Path


REPO = Path("/Users/johnny/projects/integration-test")
WORKFLOWS = REPO / ".claude" / "workflows"

# Per-phase expected artifact paths and commit message fragments
EXPECTED = {
    "phase4-testing.js": {
        "paths": ["04-testing", ".methodology/bug_hunt_report.json", ".methodology/bug_hunt_targets.json", ".methodology/decision_logs"],
        "commit_msg": "chore(p4): test-plan + coverage + bug-hunt artifacts",
    },
    "phase5-verification.js": {
        "paths": ["05-verification", ".methodology"],
        "commit_msg": "chore(p5): baseline + verification-report artifacts",
    },
    "phase7-risk.js": {
        "paths": ["07-risk", ".methodology"],
        "commit_msg": "chore(p7): risk-register artifacts",
    },
    "phase8-config.js": {
        "paths": ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md", ".methodology"],
        "commit_msg": "chore(p8): config-records + release-checklist artifacts",
    },
}


def _read_workflow(filename: str) -> str:
    return (WORKFLOWS / filename).read_text(encoding="utf-8")


def _extract_artifacts_commit_block(content: str) -> str | None:
    """Find the `phase('Artifacts Commit')` block and return its inner agent-call string."""
    # Match the agent() call inside the Artifacts Commit phase block. The block
    # ends at the `)` that closes the agent() invocation (not the options object).
    m = re.search(
        r"phase\('Artifacts Commit'\)(.*?)\n\)\n",
        content,
        re.DOTALL,
    )
    return m.group(1) if m else None


class TestWorkflowArtifactsCommitPattern:
    """Each phase workflow JS that produces deterministic artifacts before its
    exit push must have an Artifacts Commit phase with explicit path allowlist."""

    def test_phase4_has_artifacts_commit_phase(self):
        """Regression anchor: phase4 had this pattern since d4f4724."""
        content = _read_workflow("phase4-testing.js")
        assert "phase('Artifacts Commit')" in content
        block = _extract_artifacts_commit_block(content)
        assert block is not None, "phase4 must have an Artifacts Commit agent block"
        for path in EXPECTED["phase4-testing.js"]["paths"]:
            assert path in block, f"phase4 allowlist missing path: {path}"
        assert EXPECTED["phase4-testing.js"]["commit_msg"] in block

    def test_phase5_has_artifacts_commit_phase(self):
        content = _read_workflow("phase5-verification.js")
        assert "phase('Artifacts Commit')" in content
        block = _extract_artifacts_commit_block(content)
        assert block is not None, "phase5 must have an Artifacts Commit agent block"
        for path in EXPECTED["phase5-verification.js"]["paths"]:
            assert path in block, f"phase5 allowlist missing path: {path}"
        assert EXPECTED["phase5-verification.js"]["commit_msg"] in block

    def test_phase7_has_artifacts_commit_phase(self):
        content = _read_workflow("phase7-risk.js")
        assert "phase('Artifacts Commit')" in content
        block = _extract_artifacts_commit_block(content)
        assert block is not None, "phase7 must have an Artifacts Commit agent block"
        for path in EXPECTED["phase7-risk.js"]["paths"]:
            assert path in block, f"phase7 allowlist missing path: {path}"
        assert EXPECTED["phase7-risk.js"]["commit_msg"] in block

    def test_phase8_has_artifacts_commit_phase(self):
        content = _read_workflow("phase8-config.js")
        assert "phase('Artifacts Commit')" in content
        block = _extract_artifacts_commit_block(content)
        assert block is not None, "phase8 must have an Artifacts Commit agent block"
        for path in EXPECTED["phase8-config.js"]["paths"]:
            assert path in block, f"phase8 allowlist missing path: {path}"
        assert EXPECTED["phase8-config.js"]["commit_msg"] in block


class TestMetaPhasesIncludesArtifactsCommitTitle:
    """meta.phases array must include the title for the UI progress display."""

    def test_phase5_meta_phases_includes_title(self):
        content = _read_workflow("phase5-verification.js")
        # Find meta.phases array
        m = re.search(r"phases:\s*\[(.*?)\]", content, re.DOTALL)
        assert m is not None
        assert "Artifacts Commit" in m.group(1), \
            "phase5 meta.phases must include { title: 'Artifacts Commit' }"

    def test_phase7_meta_phases_includes_title(self):
        content = _read_workflow("phase7-risk.js")
        m = re.search(r"phases:\s*\[(.*?)\]", content, re.DOTALL)
        assert m is not None
        assert "Artifacts Commit" in m.group(1), \
            "phase7 meta.phases must include { title: 'Artifacts Commit' }"

    def test_phase8_meta_phases_includes_title(self):
        content = _read_workflow("phase8-config.js")
        m = re.search(r"phases:\s*\[(.*?)\]", content, re.DOTALL)
        assert m is not None
        assert "Artifacts Commit" in m.group(1), \
            "phase8 meta.phases must include { title: 'Artifacts Commit' }"


class TestArtifactPathsUseExplicitAllowlist:
    """The pattern must use explicit path lists — `git add -A` would sweep
    unrelated mid-workflow noise. Also must use `|| true` for idempotency."""

    def test_no_git_add_dash_A_in_artifacts_commit(self):
        """`git add -A` is forbidden inside the Artifacts Commit agent prompts."""
        for filename in EXPECTED:
            content = _read_workflow(filename)
            block = _extract_artifacts_commit_block(content) or ""
            assert "git add -A" not in block, \
                f"{filename} Artifacts Commit must NOT use `git add -A`"

    def test_idempotent_double_pipe_true(self):
        """`|| true` ensures the no-op path (nothing to commit) doesn't fail the phase."""
        for filename in EXPECTED:
            content = _read_workflow(filename)
            block = _extract_artifacts_commit_block(content) or ""
            assert "|| true" in block, \
                f"{filename} Artifacts Commit must use `|| true` for idempotency"
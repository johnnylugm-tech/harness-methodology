"""Playbook §4 runtime-convention lint for the 8 generated workflow phase
files (Round 11 station 5). Guards against `scripts/workflowgen/` ever
regenerating a construct the Claude Code Workflow runtime rejects at
load time — see docs/WORKFLOW_PLAYBOOK.md §4 and
scripts/workflow_audit/js_lint.py's module docstring for why this is a
comment/string-aware scan rather than a substring search.

`bug-hunt-crg.js` and `standalone-mutmut.js` are intentionally out of
scope: they are not among the 8 phase files and are not
workflowgen-generated (Round 11 plan's 明確不做 list).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.workflow_audit.js_lint import find_banned_constructs, strip_comments_and_strings

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".claude" / "workflows"
MAX_BYTES = 524288  # 512 KiB — playbook §4 hard error (validator + runtime)

PHASE_FILES = [
    "phase1-requirements.js",
    "phase2-architecture.js",
    "phase3-implementation.js",
    "phase4-testing.js",
    "phase5-verification.js",
    "phase6-quality.js",
    "phase7-risk.js",
    "phase8-config.js",
]


def _read(filename: str) -> str:
    return (WORKFLOWS_DIR / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", PHASE_FILES)
def test_no_banned_runtime_constructs(filename):
    violations = find_banned_constructs(_read(filename))
    assert not violations, f"{filename}: playbook §4 banned construct(s) found: {violations}"


@pytest.mark.parametrize("filename", PHASE_FILES)
def test_under_512kb_hard_cap(filename):
    size = len(_read(filename).encode("utf-8"))
    assert size <= MAX_BYTES, f"{filename}: {size} bytes exceeds the {MAX_BYTES}-byte runtime parse limit"


@pytest.mark.parametrize("filename", PHASE_FILES)
def test_meta_is_first_statement(filename):
    stripped = strip_comments_and_strings(_read(filename)).lstrip()
    assert stripped.startswith("export const meta"), (
        f"{filename}: `export const meta` is not the first statement "
        f"(validator hard error) — found: {stripped[:40]!r}"
    )


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not found on PATH — syntax gate needs Node.js (dev-only dependency)",
)
@pytest.mark.parametrize("filename", PHASE_FILES)
def test_node_check_syntax(filename):
    result = subprocess.run(
        ["node", "--check", str(WORKFLOWS_DIR / filename)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"{filename}: node --check failed:\n{result.stderr}"

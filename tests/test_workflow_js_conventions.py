"""Playbook §4 runtime-convention lint for the generated workflow files —
the 8 phase files (Round 11 station 5) plus run-all.js, which inlines all
eight of them (Round 23 站2). Guards against `scripts/workflowgen/` ever
regenerating a construct the Claude Code Workflow runtime rejects at
load time — see docs/WORKFLOW_PLAYBOOK.md §4 and
scripts/workflow_audit/js_lint.py's module docstring for why this is a
comment/string-aware scan rather than a substring search.

`bug-hunt-crg.js` and `standalone-mutmut.js` are intentionally out of
scope: they are not among the 8 phase files and are not
workflowgen-generated (Round 11 plan's 明確不做 list).
"""
from __future__ import annotations

import re
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

# run-all.js inlines all eight bodies into one file (Round 23 站2). It is
# workflowgen-generated like the others, so every convention below applies to
# it too — and the 512 KB cap applies with far less headroom, which is why it
# additionally carries its own ratchet.
RUNALL_FILE = "run-all.js"
GENERATED_FILES = [*PHASE_FILES, RUNALL_FILE]

# Headroom ratchet, separate from the runtime's hard cap. run-all grows at
# roughly eight times the rate of any single phase file, and the failure mode
# at 512 KB is the runtime refusing to parse — not a warning. Raising this
# number is a deliberate act: the right first response to hitting it is to
# shorten prompts in scripts/workflowgen/, not to move the ceiling.
RUNALL_MAX_BYTES = 340000  # 2026-07-28: sized to the initial 316547 bytes + 7%


def _read(filename: str) -> str:
    return (WORKFLOWS_DIR / filename).read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_no_banned_runtime_constructs(filename):
    violations = find_banned_constructs(_read(filename))
    assert not violations, f"{filename}: playbook §4 banned construct(s) found: {violations}"


@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_under_512kb_hard_cap(filename):
    size = len(_read(filename).encode("utf-8"))
    assert size <= MAX_BYTES, f"{filename}: {size} bytes exceeds the {MAX_BYTES}-byte runtime parse limit"


@pytest.mark.parametrize("filename", GENERATED_FILES)
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
@pytest.mark.parametrize("filename", GENERATED_FILES)
def test_node_check_syntax(filename, tmp_path):
    """Parse each file the way the RUNTIME parses it.

    Round 23 站2 — this test used to run `node --check <file>` directly and
    was a dead guard: a `.js` path with no package.json "type" is parsed as
    CommonJS, `export const meta` is a syntax error immediately, and
    `node --check` returns 0 anyway. Verified against a file containing a
    deliberate unescaped-apostrophe error — exit 0, no diagnostic. Every
    workflow file starts with `export const meta`, so the check could never
    fail for any of them.

    The runtime evaluates the file body with top-level await and top-level
    return, i.e. as a function body — which is exactly what
    scripts/workflowgen/js_src/sim_runner.mjs reproduces. Wrapping the same
    way before `node --check` makes this a real parse of real script text.
    (The bug this now catches is not hypothetical: run-all's first draft put
    an apostrophe inside meta.description and broke the whole file.)
    """
    src = _read(filename)
    body = re.sub(r"^export const meta", "const meta", src, count=1, flags=re.MULTILINE)
    assert body != src, f"{filename}: no `export const meta` to unwrap"
    wrapped = tmp_path / "wrapped.cjs"
    wrapped.write_text(
        "(async function (agent, phase, log, args, budget) {\n" + body + "\n})\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", "--check", str(wrapped)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"{filename}: node --check failed:\n{result.stderr}"


def test_runall_stays_within_its_headroom_ratchet():
    """The 512 KB cap above is the cliff; this is the guard rail.

    run-all inlines eight bodies, so it absorbs eight files worth of
    growth. Hitting this means shortening prompts, not raising the number
    — see RUNALL_MAX_BYTES.
    """
    size = len(_read(RUNALL_FILE).encode("utf-8"))
    assert size <= RUNALL_MAX_BYTES, (
        f"{RUNALL_FILE}: {size} bytes over the {RUNALL_MAX_BYTES}-byte headroom "
        f"ratchet ({100 * size / MAX_BYTES:.0f}% of the runtime cap)"
    )


def test_node_check_wrapper_actually_rejects_broken_syntax(tmp_path):
    """Negative control for the wrapper above — without it, this passes."""
    broken = tmp_path / "broken.cjs"
    broken.write_text(
        "(async function () {\nconst meta = { d: 'it's broken' }\n})\n", encoding="utf-8",
    )
    assert subprocess.run(
        ["node", "--check", str(broken)], capture_output=True, text=True, timeout=30,
    ).returncode != 0

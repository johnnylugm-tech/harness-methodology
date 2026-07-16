"""Bridge to `node --test` for the pure JS functions in
scripts/workflowgen/js_src/ — "any changed line runs its unit tests"
applied to the JS side of workflowgen, not just the Python generator.

Skips (does not fail) when node is unavailable, since node is a dev-only
dependency for authoring workflow JS, not a runtime dependency of the
Python framework itself.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_SRC_DIR = Path(__file__).resolve().parent.parent / "scripts" / "workflowgen" / "js_src"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not found on PATH — js_src unit tests need Node.js (dev-only dependency)",
)


def test_node_test_suite_passes():
    # sim_runner.test.mjs is excluded: it is the whole-file simulation
    # testbed with its own dedicated bridge (tests/test_workflow_sim.py,
    # 120s budget) — including it here would run it twice per pytest pass
    # and couple this snappy pure-function suite to the sim's growth.
    test_files = sorted(
        str(p) for p in JS_SRC_DIR.glob("*.test.mjs")
        if p.name != "sim_runner.test.mjs"
    )
    assert test_files, f"no *.test.mjs files found under {JS_SRC_DIR}"
    result = subprocess.run(
        ["node", "--test", *test_files],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"node --test {test_files} failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

"""Bridge to the dynamic-workflow simulation testbed (Round 12 站1).

Runs `node --test scripts/workflowgen/js_src/sim_runner.test.mjs`, which
executes all 8 generated .claude/workflows/*.js files under mocked runtime
globals (agent/phase/log/args/budget + top-level await/return semantics).

This closes the coverage gap commit cef32c4 (v2.13.3) named as the root
cause of the v2.13.2 production crash: "the test harness has no coverage
for the dynamic-workflow execution substrate". Scenario classes: happy
path through every declared phase, null-agent (session-limit shape),
hallucinated schema verdicts, JSON-less A/B responses, schema replies
missing required fields, plus regression pins for the two migration
ReferenceErrors this testbed caught on its first run (phase4 p4MidPushed,
phase6 MAX_OUTER_ATTEMPTS).

HONEST BOUNDARY: the sim exercises workflow-JS LOGIC only. OS sandbox /
permission-wall behaviour is covered by the spawn-substrate preflight
probe at run-phase entry (Round 12 站0b); live LLM behaviour only by real
E2E runs.

Kept separate from tests/test_workflowgen_js_units.py (pure-function unit
bridge) so each suite runs exactly once with its own timeout budget — the
sim executes 8 whole workflow files per scenario class and grows with
scenario count, while the unit suite must stay snappy.

Skips (does not fail) when node is unavailable — node is a dev-only
dependency for authoring workflow JS, not a runtime dependency of the
Python framework.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SIM_TEST = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "workflowgen" / "js_src" / "sim_runner.test.mjs"
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not found on PATH — sim testbed needs Node.js (dev-only dependency)",
)


def test_sim_testbed_passes():
    assert SIM_TEST.exists(), f"missing {SIM_TEST}"
    result = subprocess.run(
        ["node", "--test", str(SIM_TEST)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"sim testbed failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    # Coverage floor: happy + null-agent must each span all 8 phase files
    # (2×8) plus the pinned scenario tests — a silently shrunken suite is a
    # dead guard, the exact failure mode Round 11 station 0 revived.
    m = re.search(r"^# pass (\d+)$", result.stdout, re.MULTILINE)
    assert m, f"could not find node --test pass count in output:\n{result.stdout[-500:]}"
    # Floor raised 21 -> 33 by Round 18 站4, which added 13 scenarios: the
    # Gate 2/3/4 PASS semantics (4 per exit gate x 3 gates) that render_gate_
    # loop had zero coverage for, plus the P2 peer-review approval log.
    # 33 -> 41 by Round 22 站1: 2 scenarios x 4 FR-loop phases pinning that
    # ORCH-POST costs one dispatch per phase, not one per FR, and that the
    # total dispatch count is identical at 5 and 20 FRs. Every prior scenario
    # ran a single FR, so a per-FR dispatch cost was invisible here.
    # 41 -> 48 by Round 22 站2: 2 scenarios pinning where manifest-integrity
    # dispatches may still appear (phase3's Gate-2 loop, phase8's Final Push)
    # and where they must not (entry point, Advance loop — advance-phase
    # enforces it itself now).
    # 48 -> 51 by Round 22 站3: 3 scenarios pinning that Load FRs costs one
    # dispatch on a clean read, that phase3 still force-regenerates on attempt
    # 1 (Fix D, stale lessons), and that a failed read still reaches the regen
    # path the removed probe used to be the only trigger for.
    assert int(m.group(1)) >= 51, f"sim suite shrank: only {m.group(1)} passing tests (floor 51)"

"""Round 115 站4 — Phase 8 is complete when its milestone landed AND the phase advanced.

P8's Final Push was a hand-rolled loop: step 0 stopped as soon as
`last_milestone_head.p8` held a sha ("already pushed"), so a round after a
push that landed and an advance-phase that failed skipped the advance, and
the loop's PASS (`p8Ok`) read only the milestone — the workflow then logged
"push-milestone + advance-phase complete" with `current_phase` still 8.
Round 95 extracted the same shape from P6 (a guard that meant "done" for one
step and short-circuited the next); P8's copy was not swept. P7 already runs
`render_milestone` + `render_advance_loop`, whose guard is `current_phase >= N`.

The archive step also told the agent to delete Phase 9 references from
HANDOVER.md ("Phase 8 is final"); Phase 9 (Maintenance) is a phase of the
topology, and taskq-open's 23f41e0 removed `phase9_plan.md` from it.

The Final Push loop carried the only manifest-integrity check in front of a
milestone push, and push-milestone commits `.methodology/` wholesale for
every milestone type (P5 and P7 had no check). The check moves into
push-milestone itself — Round 22's move of the same check into advance-phase.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_p8_ends_with_the_shared_milestone_and_advance_blocks():
    from scripts.workflowgen.generate_workflows import generate

    js = generate(8)
    assert "final-push-r" not in js and "p8Ok" not in js
    assert "push-milestone --type p8" in js
    assert "advance-phase --completed 8" in js
    assert '[ "$PHASE" -ge 9 ]' in js, "the advance guard is the phase cursor, not the milestone"


def test_p8_no_longer_deletes_phase_9_from_the_handover():
    from scripts.workflowgen.generate_workflows import generate

    js = generate(8)
    assert "remove the Phase 9 references" not in js
    assert "Phase 8 is final" not in js


def _project(tmp_path: Path, manifest_text: str) -> Path:
    proj = tmp_path / "proj"
    (proj / ".methodology").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    (proj / ".methodology" / "state.json").write_text(json.dumps({"current_phase": 7}))
    (proj / ".methodology" / "quality_manifest.json").write_text(manifest_text)
    return proj


def test_push_milestone_refuses_a_corrupted_manifest_before_any_side_effect(tmp_path):
    proj = _project(tmp_path, '{"fr_ids": ["FR-01"], "fr_module_traceability"')
    state = (proj / ".methodology" / "state.json").read_bytes()
    out = subprocess.run([sys.executable, str(REPO / "harness_cli.py"), "push-milestone",
                          "--type", "p7", "--project", str(proj), "--no-git"],
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode != 0, out.stdout
    assert "[BLOCKED]" in out.stdout and "quality_manifest.json" in out.stdout
    # The milestone's own writes: HANDOVER.md, state.json's milestone record,
    # .gitignore. (harness_cli's per-invocation heartbeat is not the command's.)
    assert not (proj / "HANDOVER.md").exists()
    assert not (proj / ".gitignore").exists()
    assert (proj / ".methodology" / "state.json").read_bytes() == state

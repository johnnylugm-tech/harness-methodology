"""Give a fixture project the milestone records advance-phase now requires.

advance-phase refuses to close P3/P4/P5/P7/P8 unless push-milestone recorded
that phase's milestone in `state.json.last_milestone_head`, which it does only
after the push lands on a green build (or a repo with no CI to ask). Tests that
exercise OTHER advance checks get the record a real run's push-milestone would
have written; the blocking behaviour itself is pinned in
tests/test_ci_measures_and_advance_waits_for_green.py.
"""

from __future__ import annotations

import json
from pathlib import Path


def record_milestones_landed_green(project: Path, current_phase: int) -> None:
    """Merge every exit milestone into the fixture's state.json.

    A fixture with no state.json gets `current_phase` too: a state file without
    it reads as phase 0, which other checks (SAB drift's expected-in-phase)
    treat differently from a project actually closing *current_phase*.
    """
    from cli.advance_checks import _EXIT_MILESTONE

    state_path = Path(project) / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = (json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file()
             else {"current_phase": current_phase})
    recorded = state.get("last_milestone_head") or {}
    recorded.update({milestone: "0" * 40 for milestone in _EXIT_MILESTONE.values()})
    state["last_milestone_head"] = recorded
    state_path.write_text(json.dumps(state), encoding="utf-8")

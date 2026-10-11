"""Round 118 站5 — the entry preview names what the next phase's entry will block on.

Round 107 made `unread_config_key` "informational through P3, blocking from P4
entry" — the family policy of `config_liveness` and `drift_detection`. The other
two are in `_DELAYED_BLOCKING_PREFLIGHTS`, the catalogue
`preview_next_phase_blocking` reads; `spec_alignment` was not. So the preview
reported "Phase 4 entry: clean", `advance-phase` (which refuses an advance while
the preview is non-empty, Round 43 站2) advanced, and the project entered Phase 4
holding five obligations that `run-phase --phase 4` blocks on.

taskq-retry, 2026-10-12: `TASKQ_CORS_ORIGINS`, `TASKQ_HOST`, `TASKQ_PORT`,
`TASKQ_LOG_LEVEL`, `TASKQ_LOG_FORMAT` declared by SPEC.md, read by nothing under
src/. Replayed on the tree advance-phase was handed: the current harness
advanced (rc 0); with the member registered it refuses (rc 37).

The preview builds a sibling at `phase=next_phase` and `spec_alignment` reads
nothing but the project's files and that phase, so the preview and
`run-phase N+1` are the same question by construction. These tests pin that the
obligations it names are exactly the errors blocking at that phase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
from core.phase_hooks import _DELAYED_BLOCKING_PREFLIGHTS, PhaseHooks  # noqa: E402

from tests.test_spec_alignment import (  # noqa: E402
    _CANON_3,
    _CFG_SPEC,
    _project,
    _project_with_loader,
)

pytestmark = [pytest.mark.core]


def _preview(project: Path, completed: int):
    hooks = PhaseHooks(str(project), phase=completed, enable_kill_switch=False)
    return [o for o in hooks.preview_next_phase_blocking(completed + 1)
            if o.check_id == "spec_alignment"]


def test_spec_alignment_is_a_delayed_blocking_member() -> None:
    assert "spec_alignment" in _DELAYED_BLOCKING_PREFLIGHTS


def test_the_p4_preview_names_each_unread_config_key(tmp_path: Path) -> None:
    proj = _project_with_loader(tmp_path, _CFG_SPEC, "### FR-01: login\n")
    found = _preview(proj, 3)
    assert sorted(o.rule_id for o in found) == ["TASKQ_DB_URL", "TASKQ_PORT"]
    assert all(o.rule_id in o.message for o in found), (
        "an obligation that does not name its key leaves the fixer nothing to open"
    )
    assert all(o.target_phase == 4 for o in found)


def test_the_p3_preview_does_not_name_config_keys_that_only_block_from_p4(
    tmp_path: Path,
) -> None:
    """At P3 entry an unread key is informational; naming it would be a finding
    `run-phase 3` does not make."""
    proj = _project_with_loader(tmp_path, _CFG_SPEC, "### FR-01: login\n")
    assert _preview(proj, 2) == []


def test_a_project_that_reads_its_keys_has_no_obligation(tmp_path: Path) -> None:
    proj = _project_with_loader(tmp_path, _CFG_SPEC, "### FR-01: login\n")
    cfg = proj / "03-development" / "src" / "taskq_api" / "config.py"
    cfg.write_text(
        "import os\nDB = os.environ['TASKQ_DB_URL']\nPORT = os.environ['TASKQ_PORT']\n",
        encoding="utf-8")
    assert _preview(proj, 3) == []


def test_a_requirement_dropped_from_the_srs_is_previewed_one_phase_early(
    tmp_path: Path,
) -> None:
    proj = _project(tmp_path, canonical=_CANON_3, srs="### FR-01: login\n")
    found = _preview(proj, 1)
    run_phase = PhaseHooks(str(proj), phase=2,
                           enable_kill_switch=False).preflight_spec_alignment()
    assert run_phase["passed"] is False and run_phase["blocking"] is True
    assert len(found) == len(run_phase["error_details"]) == 2


def test_every_previewed_obligation_is_one_the_next_phase_blocks_on(
    tmp_path: Path,
) -> None:
    """The invariant behind the rest: error_details is non-empty exactly when
    the preflight at that phase fails and says it blocks."""
    proj = _project_with_loader(tmp_path, _CFG_SPEC, "### FR-01: login\n")
    for phase in range(1, 9):
        res = PhaseHooks(str(proj), phase=phase,
                         enable_kill_switch=False).preflight_spec_alignment()
        blocks = (not res["passed"]) and bool(res.get("blocking"))
        assert bool(res["error_details"]) == blocks, f"phase {phase}"


def test_the_preview_cli_reports_the_obligation(tmp_path: Path, capsys) -> None:
    import argparse

    from cli.phase_cmds import cmd_preview_next_phase

    proj = _project_with_loader(tmp_path, _CFG_SPEC, "### FR-01: login\n")
    rc = cmd_preview_next_phase(argparse.Namespace(project=str(proj), phase=3))
    out = capsys.readouterr().out
    assert rc == 1 and "[spec_alignment] TASKQ_PORT" in out


def test_advance_refuses_to_enter_a_phase_whose_entry_will_block(
    tmp_path: Path, capsys,
) -> None:
    import argparse

    from cli.advance_steps import _advance_step_refuse_open_obligations
    from cli.exit_codes import EX_ADVANCE_ENTRY_OBLIGATIONS

    proj = _project_with_loader(tmp_path, _CFG_SPEC, "### FR-01: login\n")
    obligations = _preview(proj, 3)
    rc = _advance_step_refuse_open_obligations(
        obligations, argparse.Namespace(completed_phase=3), 4, proj)
    assert rc == EX_ADVANCE_ENTRY_OBLIGATIONS
    assert "TASKQ_PORT" in capsys.readouterr().out

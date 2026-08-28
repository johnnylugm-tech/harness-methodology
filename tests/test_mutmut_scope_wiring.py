"""Round 30 站2 — the SAB→setup.cfg→mutmut chain, end to end.

Round 29 built `resolve_mutation_scope` and `write_paths_to_mutate` and wired
neither. `write_paths_to_mutate` had zero callers while its own file header
promised "auto-generated ... during advance-phase P2→P3 handoff" and "Human
edits will be overwritten on the next P2→P3 advance" — describing a hook that
did not exist and a behaviour nothing enforced.

It also had a defect that no test could see, because the only test asserted the
returned STRING and never resolved it against a filesystem: the paths carried no
source root. Probed on a fixture matching taskq-advance's SAB:

    paths            = 'taskq_plus/service, taskq_plus/storage'
    cwd / paths      = <project>/taskq_plus/service, taskq_plus/storage
    src_dir.exists() = False

`compute_mutation_score` aborts on exactly that check, so a populated
`scope_layers` would still have scored mutation_testing 0 — same verdict,
different message. The station-2 fix would have looked applied and changed
nothing.

Every test here therefore resolves what it derives against real directories.
"""
from __future__ import annotations

import json

import pytest

import harness_cli  # noqa: F401  entry-first load order
from cli.phase_cmds import _regenerate_mutmut_scope  # noqa: E402
from core.quality_gate.mutation_enforcer import _resolve_mutmut_workdir  # noqa: E402
from core.quality_gate.mutmut_scope import (  # noqa: E402
    mutate_dirs,
    resolve_mutation_scope,
    write_paths_to_mutate,
)

pytestmark = [pytest.mark.core]

SRC_ROOT = "03-development/src"

# taskq-advance's SAB, trimmed to the fields this chain reads.
_SAB = {
    "nfr_dimension_mapping": {"NFR-08": "mutation_testing"},
    "nfr_traceability": {
        "NFR-08": {
            "type": "mutation",
            "dimension": "mutation_testing",
            "target": "mutation score >= 70 over service/ + storage/",
            "scope_layers": ["service", "storage"],
            "module": "taskq_plus.service.executor",
        }
    },
    "layers": [
        {"name": "service", "modules": [
            {"name": "taskq_plus.service.executor"},
            {"name": "taskq_plus.service.breaker"},
            "taskq_plus.service",
        ]},
        {"name": "storage", "modules": [
            {"name": "taskq_plus.storage.task_store"},
            "taskq_plus.storage",
        ]},
        {"name": "cli", "modules": ["taskq_plus.cli"]},
    ],
}


def _project(tmp_path, sab: dict | None = _SAB, layers=("service", "storage", "cli")):
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    for layer in layers:
        (tmp_path / SRC_ROOT / "taskq_plus" / layer).mkdir(parents=True, exist_ok=True)
    if sab is not None:
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps(sab), encoding="utf-8"
        )
    return tmp_path


# ── the defect the Round 29 test could not see ──────────────────────────

def test_derived_scope_resolves_to_real_directories(tmp_path):
    """Not "the string looks right" — the directories must exist."""
    project = _project(tmp_path)
    paths = resolve_mutation_scope(_SAB, SRC_ROOT)
    assert paths is not None
    dirs = mutate_dirs(project, paths)
    assert dirs, "a derived scope with no directories is not a scope"
    for d in dirs:
        assert d.is_dir(), (
            f"{d} does not exist — this is the Round 29 defect: the paths "
            f"carried no source root, so compute_mutation_score aborted before "
            f"mutmut ever ran"
        )


def test_derived_scope_excludes_the_layers_the_spec_left_out(tmp_path):
    _project(tmp_path)
    paths = resolve_mutation_scope(_SAB, SRC_ROOT)
    assert paths is not None
    assert "taskq_plus/service" in paths
    assert "taskq_plus/storage" in paths
    assert "taskq_plus/cli" not in paths, (
        "cli is not in scope_layers — mutating it is the 1538 extra lines that "
        "pushed taskq-advance's Gate 2 past its budget"
    )


def test_no_scope_layers_returns_none(tmp_path):
    sab = json.loads(json.dumps(_SAB))
    del sab["nfr_traceability"]["NFR-08"]["scope_layers"]
    assert resolve_mutation_scope(sab, SRC_ROOT) is None


# ── mutate_dirs: the comma-separated value, split in ONE place ──────────

def test_mutate_dirs_splits_the_comma_separated_value(tmp_path):
    """`cwd / <whole comma string>` names a directory that cannot exist as soon
    as a project declares more than one path. Both callers used to do that."""
    dirs = mutate_dirs(tmp_path, "a/b, c/d ,e/f")
    assert dirs == [tmp_path / "a/b", tmp_path / "c/d", tmp_path / "e/f"]


# ── the generator, and what it stages ───────────────────────────────────

def test_p2_handoff_writes_the_scope_into_setup_cfg(tmp_path):
    project = _project(tmp_path)
    assert _regenerate_mutmut_scope(project) is True
    cfg = (project / "setup.cfg").read_text(encoding="utf-8")
    assert "[mutmut]" in cfg
    assert "paths_to_mutate" in cfg
    assert "auto-generated from SAB scope_layers" in cfg


def test_generated_scope_is_what_mutation_time_reads(tmp_path):
    """The whole point of the handoff: one value, written once, read once."""
    project = _project(tmp_path)
    _regenerate_mutmut_scope(project)
    cwd, paths = _resolve_mutmut_workdir(project)
    dirs = mutate_dirs(cwd, paths)
    assert [d.name for d in dirs] == ["service", "storage"]
    assert all(d.is_dir() for d in dirs)


def test_second_advance_is_a_no_op_when_the_scope_is_unchanged(tmp_path):
    project = _project(tmp_path)
    assert _regenerate_mutmut_scope(project) is True
    assert _regenerate_mutmut_scope(project) is False, (
        "an unchanged file must not be reported as written — the caller stages "
        "on this flag, and staging a no-op puts an empty change in every advance"
    )


def test_hand_edited_scope_is_overwritten_but_leaves_a_ledger_line(tmp_path):
    """The header comment promises hand edits get overwritten. An overwrite
    nobody can see afterwards is how a deliberate local change becomes an
    unexplained gate result."""
    project = _project(tmp_path)
    (project / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src\n", encoding="utf-8"
    )
    assert _regenerate_mutmut_scope(project) is True
    ledger = (project / ".methodology" / "degradations.jsonl").read_text(encoding="utf-8")
    assert "paths_to_mutate replaced" in ledger
    assert "03-development/src" in ledger


def test_missing_directories_refuse_to_write_a_broken_config(tmp_path):
    """A setup.cfg naming absent directories makes compute_mutation_score abort
    at Gate 2 pointing at mutmut. Fail here, where the SAB is in view."""
    project = _project(tmp_path, layers=("cli",))  # no service/ or storage/
    assert _regenerate_mutmut_scope(project) is False
    assert not (project / "setup.cfg").exists()
    ledger = (project / ".methodology" / "degradations.jsonl").read_text(encoding="utf-8")
    assert "non-existent" in ledger


def test_absent_scope_declaration_is_recorded_not_silent(tmp_path):
    sab = json.loads(json.dumps(_SAB))
    del sab["nfr_traceability"]["NFR-08"]["scope_layers"]
    project = _project(tmp_path, sab=sab)
    assert _regenerate_mutmut_scope(project) is False
    ledger = (project / ".methodology" / "degradations.jsonl").read_text(encoding="utf-8")
    assert "no scope_layers" in ledger


def test_no_sab_is_not_an_error(tmp_path):
    """P1/P2 projects have no SAB yet; that is not a degradation."""
    project = _project(tmp_path, sab=None)
    assert _regenerate_mutmut_scope(project) is False
    assert not (project / ".methodology" / "degradations.jsonl").exists()


def test_advance_phase_actually_calls_the_generator():
    """The call SITE, not just the callee.

    Every other test in this file calls `_regenerate_mutmut_scope` directly, so
    deleting its one line from `cmd_advance_phase` leaves them all green — which
    is precisely how Round 29 shipped a `write_paths_to_mutate` with zero
    callers and a full test file around it. Verified: removing the call site
    kept 12/12 passing until this test existed.

    Source-level on purpose. Driving a real P2→P3 advance needs a git repo, a
    manifest, a SAD and every precheck; that test belongs to advance-phase, not
    to the mutation scope. What must not silently disappear is the wiring, and
    the wiring is one line.
    """
    import inspect

    from cli.phase_cmds import _advance_commit_targets

    # Round 81 站7: the advance pipeline is `cmd_advance_phase` plus the
    # `_advance_*` helpers extracted from it. Reading only the caller now
    # answers a question this test never meant to ask.
    from tests.support.pipeline import pipeline_source
    src = pipeline_source("cli/phase_cmds.py", "cmd_advance_phase",
                          helper_prefix="_advance_")
    assert "_regenerate_mutmut_scope(project)" in src, (
        "cmd_advance_phase no longer renders the mutation scope — setup.cfg "
        "goes stale and _resolve_mutmut_workdir silently mutates the whole tree"
    )
    assert "setup_cfg_written=" in src, (
        "the generated setup.cfg is not staged in the advance commit — the "
        "scope decision lands in an untracked file"
    )
    assert "setup_cfg_written" in inspect.signature(_advance_commit_targets).parameters


def test_write_reports_the_previous_value_it_replaced(tmp_path):
    project = _project(tmp_path)
    wrote, previous = write_paths_to_mutate(project, "a/b")
    assert (wrote, previous) == (True, None)
    wrote, previous = write_paths_to_mutate(project, "c/d")
    assert (wrote, previous) == (True, "a/b")
    wrote, previous = write_paths_to_mutate(project, "c/d")
    assert (wrote, previous) == (False, "c/d")

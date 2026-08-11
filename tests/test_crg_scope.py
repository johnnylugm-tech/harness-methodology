"""The architecture score must be measured over the delivered tree (Round 37 站0/站2).

Measured defect, taskq-renew 2026-08-05:

    graph.db                 last_build_type   files  nodes  communities  score
    local (.code-review-graph)  incremental       11    165           12   77.8
    clean clone, full build     full              47    802           32   57.1  (= CI)

`run_independent_crg` uses `code-review-graph update` whenever a graph.db is
already present. On taskq-renew that incremental graph covered 11 of the
project's 47 delivered Python files — 23%. Gate 3 and Gate 4 folded the
resulting `architecture` sub-score into their composite (Gate 4 passed at
93.6 with architecture=77.8), while CI's standalone `crg-arch-check` measured
36.4 and then 57.1 against the same commits and failed every time.

Second wound in the same code path: `cmd_finalize_gate` copies
`.sessi-work/crg_metrics.json` to `.methodology/crg_baseline_p{N}.json`
unconditionally, so 77.8 — a score below the floor the gate config itself
states — was written down as the reference for later phases.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _graph_db(tmp_path: Path, files: list[str]) -> Path:
    """A minimal graph.db shaped like code-review-graph's own schema."""
    db = tmp_path / "graph.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_path TEXT)")
    con.executemany("INSERT INTO nodes (file_path) VALUES (?)",
                    [(f,) for f in files])
    con.commit()
    con.close()
    return db


def test_graph_file_set_reads_the_files_the_graph_actually_covers(tmp_path: Path) -> None:
    from harness.crg_independent import graph_file_set

    db = _graph_db(tmp_path, ["/p/a.py", "/p/b.py", "/p/a.py"])
    assert graph_file_set(db) == {"/p/a.py", "/p/b.py"}


def test_graph_missing_delivered_sources_forces_a_full_rebuild(tmp_path: Path) -> None:
    """The taskq-renew shape: the graph knows 1 file, the project has 3."""
    from harness.crg_independent import needs_full_rebuild

    stale, residual = needs_full_rebuild(
        graph_files={"/p/a.py"},
        source_files={"/p/a.py", "/p/b.py", "/p/c.py"},
    )
    assert stale is True
    assert residual == {"/p/b.py", "/p/c.py"}


def test_graph_holding_a_deleted_file_also_forces_a_rebuild() -> None:
    """A file the graph still remembers but the project no longer delivers
    keeps contributing nodes to the community partition."""
    from harness.crg_independent import needs_full_rebuild

    stale, _ = needs_full_rebuild(
        graph_files={"/p/a.py", "/p/gone.py"},
        source_files={"/p/a.py"},
    )
    assert stale is True


def test_a_graph_that_covers_the_delivered_tree_is_not_rebuilt() -> None:
    """The measured property of a correct build: on the clean taskq-renew
    clone the two sets were equal (47 == 47, zero difference either way)."""
    from harness.crg_independent import needs_full_rebuild

    stale, residual = needs_full_rebuild(
        graph_files={"/p/a.py", "/p/b.py"},
        source_files={"/p/b.py", "/p/a.py"},
    )
    assert stale is False
    assert residual == set()


# --------------------------------------------------------------------------
# The floor, and the baseline that must clear it
# --------------------------------------------------------------------------

def test_the_architecture_floor_comes_from_the_gate_config() -> None:
    """80.0 is stated in gate3/gate4 YAML, in three spec_phase*.py generators
    (`crg_threshold=80.0`) and in the CI YAML (`--threshold 80`). Only the
    YAML is scored against, so only the YAML is the source."""
    from core.quality_gate.crg_baseline import architecture_floor
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    assert architecture_floor() == load_gate_thresholds(4)["architecture"]


def test_a_score_below_the_floor_is_not_written_as_a_baseline() -> None:
    """taskq-renew's P6 baseline was architecture_score=77.8 against a floor
    of 80 — a reference point that cannot itself pass."""
    from core.quality_gate.crg_baseline import should_write_baseline

    ok, reason = should_write_baseline({"architecture_score": 77.8})
    assert ok is False
    assert "77.8" in reason and "80" in reason


def test_a_passing_score_is_written_as_a_baseline() -> None:
    from core.quality_gate.crg_baseline import should_write_baseline

    ok, reason = should_write_baseline({"architecture_score": 92.0})
    assert ok is True
    assert reason == ""


# --------------------------------------------------------------------------
# Round 44 站0 — the shortfall was measured, recorded, and acted on by nobody
# --------------------------------------------------------------------------
#
# Round 37 站2 made the graph reconcile against the delivered tree and forced
# one full rebuild when it did not cover it. What remains after that rebuild
# is recorded and then dropped:
#
#     record_degradation(root, "crg:graph-scope",
#         f"after a full build the graph covers {len(_graph_files)} file(s) "
#         f"and the project delivers {len(_sources)} — the architecture "
#         f"score is measured over the graph's set")
#
# Round 42 站4c then made the denominator travel: `_graph_files` /
# `_source_files` reach the gate result's `calibration` block. Measured on
# taskq-advance, four such degradations were written during Phase 3 (41 files
# graphed against 47 delivered), and `grep -rn "graph_files"` over the whole
# repository finds one producer and no consumer that compares the two.
#
# `needs_full_rebuild` already fixed the predicate: equality, measured on the
# clean taskq-renew clone at 47 == 47. Station 0 premise 3 re-measured it on
# every live project — taskq 20/20, taskq-renew 47/47, taskq-api 40/40,
# taskq-advance 50/50, run-all-by-workflow 22/22. Equality is reachable, so a
# score measured over less than the delivered tree is a score of something
# else.

def test_the_metrics_name_the_files_the_graph_never_parsed() -> None:
    """A count cannot be acted on; a list can (Round 24 站1)."""
    from harness.crg_independent import graph_coverage_gap

    assert graph_coverage_gap({
        "_graph_files": 2, "_source_files": 3,
        "_unparsed_files": ["03-development/src/b.py"],
    }) == ["03-development/src/b.py"]


def test_a_fully_covered_graph_reports_no_gap() -> None:
    from harness.crg_independent import graph_coverage_gap

    assert graph_coverage_gap({
        "_graph_files": 3, "_source_files": 3, "_unparsed_files": [],
    }) == []


def test_metrics_predating_the_field_report_no_gap() -> None:
    """Round 39/40: a record older than the field is not a violation, and
    Round 32/35: could-not-measure is not a finding."""
    from harness.crg_independent import graph_coverage_gap

    assert graph_coverage_gap({"architecture_score": 91.7}) == []


def test_a_missing_score_is_not_silently_treated_as_passing() -> None:
    """Round 35's rule: a run that could not measure has no score, and no
    score is not a passing score."""
    from core.quality_gate.crg_baseline import should_write_baseline

    ok, reason = should_write_baseline({})
    assert ok is False
    assert "architecture_score" in reason


def _project_with_metrics(tmp_path: Path, score: float) -> Path:
    import json
    work = tmp_path / ".sessi-work"
    work.mkdir()
    (tmp_path / ".methodology").mkdir()
    (work / "crg_metrics.json").write_text(
        json.dumps({"architecture_score": score}), encoding="utf-8")
    return tmp_path


def test_snapshot_writes_a_passing_baseline(tmp_path: Path) -> None:
    from core.quality_gate.crg_baseline import snapshot_baseline

    project = _project_with_metrics(tmp_path, 92.0)
    assert snapshot_baseline(project, 6) is True
    assert (project / ".methodology" / "crg_baseline_p6.json").is_file()


def test_snapshot_refuses_a_sub_floor_score_and_says_so(tmp_path: Path) -> None:
    """taskq-renew's P6 exactly: 77.8 written as the reference for P7/P8."""
    from core.quality_gate.crg_baseline import snapshot_baseline

    project = _project_with_metrics(tmp_path, 77.8)
    assert snapshot_baseline(project, 6) is False
    assert not (project / ".methodology" / "crg_baseline_p6.json").exists()
    ledger = (project / ".methodology" / "degradations.jsonl")
    assert ledger.is_file() and "77.8" in ledger.read_text(encoding="utf-8")

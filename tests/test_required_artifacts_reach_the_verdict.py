"""Round 68 站0 — a file the requirement names, at the place it names.

An external review of taskq-cc took points off for two things. Both are true,
and both are the same defect one layer down.

    -5  `grep -c "^TASKQ_" .env.example` (SPEC §8 #26) — the file does not
        exist anywhere in the tree.
    -3  SPEC §6 draws `migrations/` and `alembic.ini` at the project root;
        they ship at `03-development/src/migrations/`.

Between them sits `02-architecture/SAD.md:45`, which says in so many words:

    Source directories (4 + 2 independence + migrations) — matches SPEC.md §6
    exactly

That sentence is false, and no check in this framework has ever opened the
delivered tree to read it. The requirement even wrote its own failure mode
down — SRS §2.9, on the mandatory config files: "their absence silently turns
the linked dimensions into free points" — and there was no executor for it.

Measured before choosing this shape: scraping backticked paths out of SRS.md
and SAD.md and checking they resolve gives 68 candidates on taskq-cc, of which
46 do not resolve — `app.py`, `auth.py`, `orm.py`, bare leaf names with no
directory. A guard built on that is a false-positive machine, so it was
rejected with the number rather than on taste. What is left is an explicit
list, and the SAB is where this framework already keeps explicit machine-
readable lists.

The declaration therefore sits with the judged project, which is Round 57's
mother defect and is stated here rather than hidden: what this station buys is
that a declaration which IS made is checked by something that always runs, and
that making none is a ledger row instead of free.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from harness_cli import cmd_finalize_gate


def _tree_with_a_relocated_deliverable(tmp_path: Path) -> Path:
    """taskq-cc's shape: the package and its migrations live under the source
    root, and `.env.example` is nowhere."""
    src = tmp_path / "03-development" / "src"
    (src / "shopfront").mkdir(parents=True)
    (src / "shopfront" / "__init__.py").write_text("", encoding="utf-8")
    (src / "migrations").mkdir()
    (src / "migrations" / "env.py").write_text("", encoding="utf-8")
    (src / "migrations" / "alembic.ini").write_text("", encoding="utf-8")
    (tmp_path / "Makefile").write_text("verify-system:\n\t@true\n", encoding="utf-8")
    return tmp_path


_SAB = {"required_artifacts": ["Makefile", "migrations/", "alembic.ini",
                               ".env.example"]}


def test_a_declared_artifact_that_is_absent_is_reported(tmp_path):
    from core.quality_gate.required_artifacts import (
        STATUS_MISSING, declared_artifact_findings,
    )

    findings = declared_artifact_findings(
        _tree_with_a_relocated_deliverable(tmp_path), _SAB)
    absent = [f for f in findings if f["status"] == STATUS_MISSING]

    assert [f["declared"] for f in absent] == [".env.example"], (
        f"the one declared file that is not in the tree was not reported: "
        f"{findings}"
    )


def test_a_declared_artifact_delivered_elsewhere_names_where(tmp_path):
    """"Somewhere else" is a different answer from "nowhere", and a project
    can only act on the one that says where."""
    from core.quality_gate.required_artifacts import (
        STATUS_ELSEWHERE, declared_artifact_findings,
    )

    findings = declared_artifact_findings(
        _tree_with_a_relocated_deliverable(tmp_path), _SAB)
    moved = {f["declared"]: f["found_at"]
             for f in findings if f["status"] == STATUS_ELSEWHERE}

    assert set(moved) == {"migrations/", "alembic.ini"}, (
        f"the two deliverables that ship at a path other than the declared "
        f"one were not both reported: {findings}"
    )
    assert all(v.startswith("03-development/src/") for v in moved.values()), (
        f"the finding has to say where the file actually is: {moved}"
    )


def test_an_artifact_at_its_declared_path_is_not_reported(tmp_path):
    from core.quality_gate.required_artifacts import declared_artifact_findings

    findings = declared_artifact_findings(
        _tree_with_a_relocated_deliverable(tmp_path), _SAB)

    assert "Makefile" not in [f["declared"] for f in findings], (
        f"a file delivered exactly where it was declared was reported: "
        f"{findings}"
    )


def test_a_project_that_declares_nothing_is_recorded_not_blocked(tmp_path):
    """Round 50 站4's rule, applied one layer up.

    "Nobody declared which files this project must ship" is an honest state
    and stays available. What it does not get is silence — the alternative is
    that a project which lists nothing looks identical to one that listed
    everything and delivered it.
    """
    from core.quality_gate.required_artifacts import (
        record_required_artifacts, required_artifacts_blocking_reason,
    )

    project = _tree_with_a_relocated_deliverable(tmp_path)
    (project / ".methodology").mkdir(exist_ok=True)

    rows = record_required_artifacts(project, {})
    assert required_artifacts_blocking_reason(rows) is None, (
        "a project with no declaration was blocked — this station checks "
        "declarations, it does not invent them"
    )

    ledger = (project / ".methodology" / "degradations.jsonl")
    assert ledger.is_file() and "gate:required-artifacts" in ledger.read_text(
        encoding="utf-8"), (
        "no declaration left no trace at all, so 'we did not look' reads the "
        "same as 'we looked and it was fine'"
    )


def test_the_blocking_reason_names_both_kinds(tmp_path):
    from core.quality_gate.required_artifacts import (
        record_required_artifacts, required_artifacts_blocking_reason,
    )

    project = _tree_with_a_relocated_deliverable(tmp_path)
    (project / ".methodology").mkdir(exist_ok=True)

    reason = required_artifacts_blocking_reason(
        record_required_artifacts(project, _SAB))

    assert reason, (
        "three declared deliverables are absent or somewhere else and the "
        "gate had nothing to say"
    )
    assert ".env.example" in reason and "alembic.ini" in reason, (
        f"the block has to name the paths: {reason}"
    )
    assert "03-development/src/migrations" in reason, (
        f"a relocated deliverable's block has to carry where it really is, "
        f"or the fix is a search: {reason}"
    )


def test_required_artifacts_survives_the_sab_round_trip():
    """The field has to exist on SABSpec, not just in a parser branch.

    `render_canonical_sab_template` walks `dataclasses.fields(SABSpec)` and
    raises on a field it has no branch for — that is the existing net that
    stops the canonical template from silently dropping a key. This pins the
    field into it rather than around it.
    """
    from core.quality_gate.sab_parser import (
        SABSpec, render_canonical_sab_template,
    )

    spec = SABSpec(required_artifacts=[".env.example"])
    assert spec.to_dict()["required_artifacts"] == [".env.example"], (
        "required_artifacts did not survive SABSpec.to_dict() — the parsed "
        "SAB the gate reads would not carry it"
    )
    assert "required_artifacts" in render_canonical_sab_template(), (
        "the canonical template does not teach the key, so no project would "
        "ever write one"
    )


def _finalize_over(monkeypatch, project: Path, sab: dict, *, phase=3):
    """A real Gate 1 finalize over *project*. Returns (exit_code, stdout).

    Fixture shape is tests/test_stubbed_boundary_reaches_the_verdict.py's,
    for the same reason it gives there: the question is what the VERDICT does,
    and patching finalize_gate's private seams to ask it would be the thing
    tests/test_patch_discipline.py refuses.
    """
    sessi = project / ".sessi-work"
    (sessi / "sentinels").mkdir(parents=True, exist_ok=True)
    (sessi / "gate1_result.json").write_text(json.dumps({
        "gate": 1, "phase": phase, "fr_id": "FR-01",
        "score": 95.0, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": {
            "linting": {"score": 100.0, "threshold": 90},
            "type_safety": {"score": 98.5, "threshold": 85},
        },
    }))
    (sessi / "sentinels" / f"g1_p{phase}_fr01.flag").write_text("test")

    meth = project / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}))
    (meth / "state.json").write_text(
        json.dumps({"state": "ACTIVE", "current_phase": phase}))
    (meth / "SAB.json").write_text(json.dumps(sab))

    import core.quality_gate.gate_thresholds as _gt
    import yaml as _yaml
    cfg = project / "gate1_minimal.yaml"
    cfg.write_text(_yaml.dump({
        "gate": 1,
        "dimensions": [
            {"name": "linting", "threshold": 90, "weight": 0.5},
            {"name": "type_safety", "threshold": 85, "weight": 0.5},
        ],
    }))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg)

    class Args:
        pass
    a = Args()
    a.gate = 1  # type: ignore[attr-defined]
    a.phase = phase  # type: ignore[attr-defined]
    a.project = str(project)  # type: ignore[attr-defined]
    a.fr_id = "FR-01"  # type: ignore[attr-defined]
    a.force = False  # type: ignore[attr-defined]
    a.no_git = True  # type: ignore[attr-defined]

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    try:
        code = cmd_finalize_gate(a)  # type: ignore[arg-type]
    except SystemExit as exc:
        code = exc.code
    return code, captured.getvalue()


def test_a_missing_declared_deliverable_stops_the_gate(monkeypatch, tmp_path):
    """The half that does not exist yet.

    taskq-cc published Gate 4 PASS at 95.28 with `.env.example` absent and its
    SAD asserting the tree matches a layout it does not match.
    """
    project = _tree_with_a_relocated_deliverable(tmp_path)
    code, out = _finalize_over(monkeypatch, project, _SAB)

    assert code != 0, (
        "the gate passed with a declared deliverable missing from the tree "
        "and two more shipped somewhere other than where they were declared"
    )
    assert ".env.example" in out, (
        f"the block did not name the file. Got:\n{out[-1500:]}"
    )

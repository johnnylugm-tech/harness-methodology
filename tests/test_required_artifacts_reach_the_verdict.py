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

import json
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from harness.harness_bridge import GateBlockedError, GateContext, HarnessBridge


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


def _make_ctx(tmp_path: Path, gate_num: int, *, fr_id: "str | None" = None) -> GateContext:
    """A real GateContext over tmp_path, config with no tool-execution dims.

    Fixture shape is tests/test_harness_bridge.py TestFinalizeGate's: the
    question is what the VERDICT does, and patching finalize_gate's private
    seams to ask it would be the thing tests/test_patch_discipline.py refuses.
    """
    from core.quality_gate.constitution.profile import DimensionConfig, GateConfig

    config = GateConfig(
        gate_num=gate_num, score_gate=80.0, max_rounds=3,
        dimensions=[
            DimensionConfig(name="linting", threshold=75.0),
            DimensionConfig(name="type_safety", threshold=75.0),
        ],
    )
    ssi_dir = Path(__file__).parent.parent / "harness" / "ssi"
    work_dir = tmp_path / ".sessi-work"
    work_dir.mkdir(exist_ok=True)
    return GateContext(
        gate_num=gate_num, config=config, project_root=str(tmp_path),
        phase=3, fr_id=fr_id,
        ssi_scripts_dir=str(ssi_dir / "scripts"),
        ssi_prompts_dir=str(ssi_dir / "prompts"),
        ssi_schemas_dir=str(ssi_dir / "schemas"),
        work_dir=str(work_dir),
    )


def _write_result(ctx: GateContext, data: dict) -> None:
    (Path(ctx.work_dir) / f"gate{ctx.gate_num}_result.json").write_text(
        json.dumps(data), encoding="utf-8")


def _patch_gate_config(tmp_path: Path, monkeypatch) -> None:
    """Monkeypatch gate_config_path to a config with no
    requires_tool_execution:true dimensions, so _check_tool_evidence finds
    nothing to validate (same reason as test_harness_bridge.py)."""
    import yaml as _yaml
    import core.quality_gate.gate_thresholds as _gt

    _minimal_cfg = tmp_path / "gate_minimal.yaml"
    _minimal_cfg.write_text(_yaml.dump({
        "gate": 2,
        "dimensions": [
            {"name": "linting", "threshold": 75},
            {"name": "type_safety", "threshold": 75},
        ],
    }))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: _minimal_cfg)


def _passing_result() -> dict:
    """Every dimension at 100, framework-measured — the shape of the FR-01
    gate evaluations that measured all code dims at 100 while the artifact
    stage alone blocked the finalize."""
    return {
        "overall_score": 100.0, "meets_target": True, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": {
            "linting": {"score": 100.0, "threshold": 75.0,
                        "score_source": "framework"},
            "type_safety": {"score": 100.0, "threshold": 75.0,
                            "score_source": "framework"},
        },
    }


def _project_with_a_missing_declared_deliverable(tmp_path: Path) -> Path:
    """The tree from above plus a SAB declaring `.env.example` (absent) and
    the two relocated deliverables.

    The Makefile is dropped: its `@true` recipe is exactly the tautological
    verify-system shape Round 52 blocks on, and this fixture's question is
    the artifact stage — not verify_target. With no Makefile that stage
    returns None by design, and the declared Makefile joins the absent list
    (both finalize tests below only care that SOMETHING declared is absent).
    """
    project = _tree_with_a_relocated_deliverable(tmp_path)
    (project / "Makefile").unlink()
    meth = project / ".methodology"
    meth.mkdir(exist_ok=True)
    (meth / "SAB.json").write_text(json.dumps(_SAB), encoding="utf-8")
    return project


def _finalize_over(ctx: GateContext, bridge: HarnessBridge):
    from unittest.mock import patch

    with patch.object(bridge, "_update_quality_manifest"), \
            patch.object(bridge, "_log"), patch.object(bridge, "_effort"):
        return bridge.finalize_gate(ctx)


def test_gate1_finalize_records_a_missing_deliverable_without_blocking(
        monkeypatch, tmp_path):
    """Round 102 站1 scope — a per-FR gate-1 finalize does not hard-block on
    whole-project SAB required_artifacts.

    The declared artifacts a project names at Phase 2 (alembic.ini, Makefile,
    requirements.lock, ...) are products of the implementation phase, so the
    first per-FR gate of Phase 3 fails by construction: the tree cannot ship
    what the phase that produces it has not run yet, and no FR owns another
    FR's or the project's scaffolding. taskq-done measured it: FR-01's code
    dims all 100 while 6 Phase-3-era declared files were absent, and the
    block stranded the FR until FR-02's run happened to deliver them. The
    finding must stay visible — a tree that contradicts its SAB is recorded
    at every gate — but it must not stop an FR whose own quality is done.
    """
    project = _project_with_a_missing_declared_deliverable(tmp_path)
    _patch_gate_config(tmp_path, monkeypatch)
    ctx = _make_ctx(tmp_path, gate_num=1, fr_id="FR-01")
    _write_result(ctx, _passing_result())

    result = _finalize_over(ctx, HarnessBridge())

    assert result.quality_complete is True, (
        "gate 1 finalize blocked on whole-project artifacts that no single "
        "FR can be asked to deliver"
    )
    ledger = project / ".methodology" / "degradations.jsonl"
    assert ledger.is_file(), "a missing declared deliverable left no trace"
    assert "gate:required-artifacts" in ledger.read_text(encoding="utf-8"), (
        "the finding has to stay visible even when it does not block"
    )


def test_phase_exit_finalize_still_blocks_on_a_missing_deliverable(
        monkeypatch, tmp_path):
    """The Round 68 block survives where it means something.

    taskq-cc published Gate 4 PASS at 95.28 with `.env.example` absent and its
    SAD asserting the tree matches a layout it does not match. Phase-exit and
    final gates (gate >= 2) are the first points whose workflows can deliver
    (or drop) the declared paths, so the hard block stays there.
    """
    _project_with_a_missing_declared_deliverable(tmp_path)
    _patch_gate_config(tmp_path, monkeypatch)
    ctx = _make_ctx(tmp_path, gate_num=2)
    _write_result(ctx, _passing_result())

    with pytest.raises(GateBlockedError) as exc:
        _finalize_over(ctx, HarnessBridge())
    details = exc.value.details
    assert "required_artifact_missing" in details, (
        f"the phase-exit block did not name its kind: {details}"
    )
    assert ".env.example" in str(details), (
        f"the phase-exit block did not name the file: {details}"
    )

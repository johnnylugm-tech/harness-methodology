"""Regression tests for the per-FR gate result writer.

Two defects together produced the P7 FR-09 false-positive block:

A. The Gate 1 prompt template (`cli/fr_prompts/gate.py::build_gate1_prompt`)
   did not enumerate the `architecture_constraints` dimension, so an agent
   that read the template verbatim produced a `breakdown` JSON whose
   `architecture_constraints` block lacked `tool_evidence`. The Gate 1
   validator (`harness_bridge.py::_check_tool_evidence`) correctly rejected
   that as `tool_evidence_missing`.

B. `cli/gate_cmds.py::_cmd_finalize_gate_impl` wrote the per-FR file
   (`.methodology/gate_results/gate{N}/{fr_id}.json`) using **only** the
   CLI `--fr-id` argument for the file name, with no check against the
   JSON's internal `fr_id` field. The FR-09 sub-agent ran `finalize-gate`
   directly three times with different `--fr-id` values; the same FR-09
   payload landed in `FR-06.json`, `FR-08.json`, and `FR-10.json`,
   poisoning the per-FR audit directory. The earliest of those writes
   (the 1722-byte version missing the architecture_constraints evidence)
   is the one the validator flagged.

These tests pin the writer invariants so the dual-source-of-truth bug
cannot recur without a loud test failure:

  1. fr_id consistency: if the JSON's fr_id and the CLI's --fr-id differ,
     the per-FR file is named after the JSON's fr_id (which is the
     authoritative source) and the canonical summary is still written.
  2. Idempotency: a per-FR file that already represents a completed PASS
     is not overwritten unless --force is passed.
  3. The prompt template for GATE1 enumerates all four dimensions declared
     in `gate1_per_fr.yaml`, so the agent cannot omit architecture_constraints
     by accident.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from harness_cli import cmd_finalize_gate


def _call_finalize(
    monkeypatch,
    tmp_path: Path,
    *,
    gate: int = 1,
    phase: int = 3,
    fr_id: str = "FR-01",
    force: bool = False,
    gate1_result: dict | None = None,
):
    """Replicate the integration-test fixture used by `test_handover_generator`
    so the writer invariants can be exercised without spinning up a real
    project. Returns (exit_code, captured_stdout)."""
    sessi = tmp_path / ".sessi-work"
    sessi.mkdir(parents=True, exist_ok=True)
    if gate1_result is None:
        # Default-shaped payload that satisfies the bridge's per-dimension
        # score checks. The fr_id is intentionally a separate input from the
        # CLI arg so the mismatch test can flip it.
        gate1_result = {
            "gate": 1, "phase": phase, "fr_id": fr_id,
            "score": 95.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {
                "linting": {"score": 100.0, "threshold": 90},
                "type_safety": {"score": 98.5, "threshold": 85},
                "test_coverage": {"score": 92.0, "threshold": 80},
                "architecture_constraints": {"score": 100.0, "threshold": 90},
            },
        }
    (sessi / "gate1_result.json").write_text(json.dumps(gate1_result))

    # Minimal manifest/state so the bridge does not block on missing files.
    manifest_dir = tmp_path / ".methodology"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": [fr_id], "gate_results": {"gate1": {}}})
    )
    (manifest_dir / "state.json").write_text(
        json.dumps({"state": "ACTIVE", "current_phase": phase})
    )

    # Sentinel so the writer path is reachable.
    _sentinel_key = (fr_id or "phase").replace("-", "").lower()
    _sentinel_dir = sessi / "sentinels"
    _sentinel_dir.mkdir(parents=True, exist_ok=True)
    (_sentinel_dir / f"g{gate}_p{phase}_{_sentinel_key}.flag").write_text("test")

    # Minimal gate config without requires_tool_execution so the harness
    # validator does not block on missing tool_evidence for these writer
    # tests. The architecture_constraints prompt-dimension test below lives
    # elsewhere (it does not run finalize-gate at all).
    import core.quality_gate.gate_thresholds as _gt
    import yaml as _yaml
    _minimal_cfg = tmp_path / "gate1_minimal.yaml"
    _minimal_cfg.write_text(_yaml.dump({
        "gate": 1,
        "dimensions": [
            {"name": "linting", "threshold": 90},
            {"name": "type_safety", "threshold": 85},
            {"name": "test_coverage", "threshold": 80},
            {"name": "architecture_constraints", "threshold": 90},
        ],
    }))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: _minimal_cfg)

    # Disable git ops.
    monkeypatch.setattr(
        "cli._shared._make_git",
        lambda args, project: __import__("harness.git_strategy")
        .git_strategy.GitStrategy(project, enabled=False),
    )

    class Args:
        pass
    a = Args()
    a.gate = gate  # type: ignore[reportAttributeAccessIssue]
    a.phase = phase  # type: ignore[reportAttributeAccessIssue]
    a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
    a.fr_id = fr_id  # type: ignore[reportAttributeAccessIssue]
    a.force = force  # type: ignore[reportAttributeAccessIssue]

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    try:
        exit_code = cmd_finalize_gate(a)  # type: ignore[reportArgumentType]
    except SystemExit as e:
        exit_code = e.code
    return exit_code, captured.getvalue()


def _per_fr_path(tmp_path: Path, fr_id: str) -> Path:
    return tmp_path / ".methodology" / "gate_results" / "gate1" / f"{fr_id}.json"


class TestFrIdConsistency:
    """Bug B invariant (1): the per-FR file is named after the JSON's
    fr_id, not the CLI arg, when they disagree."""

    def test_mismatch_writes_per_fr_file_under_json_owner(self, monkeypatch, tmp_path):
        # CLI says FR-09, but the .sessi-work/gate1_result.json says FR-08 —
        # the exact symptom that produced the P7 FR-09 false-positive block.
        payload = {
            "gate": 1, "phase": 3, "fr_id": "FR-08",
            "score": 95.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {
                "linting": {"score": 100.0, "threshold": 90},
                "type_safety": {"score": 98.5, "threshold": 85},
                "test_coverage": {"score": 92.0, "threshold": 80},
                "architecture_constraints": {"score": 100.0, "threshold": 90},
            },
        }
        _call_finalize(monkeypatch, tmp_path, fr_id="FR-09", gate1_result=payload)

        # The canonical summary is written under the JSON's fr_id (FR-08),
        # not the CLI arg (FR-09). This is the fix: the JSON is authoritative.
        assert _per_fr_path(tmp_path, "FR-08").exists(), (
            "per-FR file should be written under JSON-owner fr_id (FR-08), "
            "not under the CLI arg (FR-09) — the CLI arg is a route hint, "
            "not the source of truth"
        )
        assert not _per_fr_path(tmp_path, "FR-09").exists(), (
            "per-FR file must NOT be written under the CLI arg when the JSON "
            "disagrees — that is exactly the pollute-the-directory bug"
        )

    def test_mismatch_emits_blocked_but_canonical_summary_still_written(self, monkeypatch, tmp_path):
        payload = json.loads(json.dumps({
            "gate": 1, "phase": 3, "fr_id": "FR-08",
            "score": 95.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {
                "linting": {"score": 100.0, "threshold": 90},
                "type_safety": {"score": 98.5, "threshold": 85},
                "test_coverage": {"score": 92.0, "threshold": 80},
                "architecture_constraints": {"score": 100.0, "threshold": 90},
            },
        }))
        _call_finalize(monkeypatch, tmp_path, fr_id="FR-09", gate1_result=payload)

        # Canonical summary (the aggregate finalization) is still written.
        canonical = tmp_path / ".methodology" / "gate1_result.json"
        assert canonical.exists(), (
            "canonical summary must still be written even when per-FR is "
            "renamed — the worker JS reads the canonical as the gate's "
            "authoritative verdict"
        )

    def test_force_overrides_mismatch_and_writes_under_cli_arg(self, monkeypatch, tmp_path):
        payload = {
            "gate": 1, "phase": 3, "fr_id": "FR-08",
            "score": 95.0, "quality_complete": True,
            "open_critical_count": 0, "open_high_count": 0,
            "breakdown": {
                "linting": {"score": 100.0, "threshold": 90},
                "type_safety": {"score": 98.5, "threshold": 85},
                "test_coverage": {"score": 92.0, "threshold": 80},
                "architecture_constraints": {"score": 100.0, "threshold": 90},
            },
        }
        _call_finalize(monkeypatch, tmp_path, fr_id="FR-09", force=True,
                        gate1_result=payload)

        assert _per_fr_path(tmp_path, "FR-09").exists(), (
            "--force should preserve the legacy CLI-arg-as-file-name behavior "
            "for explicit operator overrides"
        )


class TestIdempotency:
    """Bug B invariant (2): a per-FR file that already represents a
    completed PASS is not overwritten unless --force is passed."""

    def test_passing_per_fr_file_is_not_overwritten(self, monkeypatch, tmp_path):
        # Pre-populate a passing FR-04.json so the second finalize-gate
        # call would clobber it without the guard.
        path = _per_fr_path(tmp_path, "FR-04")
        path.parent.mkdir(parents=True, exist_ok=True)
        original = {
            "gate": 1, "phase": 3, "fr_id": "FR-04",
            "score": 95.0, "quality_complete": True,
            "verdict": "PASS", "passed": True,
            "composite_score": 95.0,
            "breakdown": {
                "linting": {"score": 100.0, "threshold": 90},
                "type_safety": {"score": 98.5, "threshold": 85},
                "test_coverage": {"score": 92.0, "threshold": 80},
                "architecture_constraints": {"score": 100.0, "threshold": 90},
            },
        }
        path.write_text(json.dumps(original))

        _call_finalize(monkeypatch, tmp_path, fr_id="FR-04")

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == original, (
            "per-FR file with quality_complete=True and verdict=PASS must "
            "not be overwritten without --force"
        )

    def test_force_overwrites_passing_per_fr_file(self, monkeypatch, tmp_path):
        path = _per_fr_path(tmp_path, "FR-04")
        path.parent.mkdir(parents=True, exist_ok=True)
        original = {
            "gate": 1, "phase": 3, "fr_id": "FR-04",
            "score": 95.0, "quality_complete": True,
            "verdict": "PASS", "passed": True,
            "composite_score": 95.0,
            "breakdown": {
                "linting": {"score": 100.0, "threshold": 90},
                "type_safety": {"score": 98.5, "threshold": 85},
                "test_coverage": {"score": 92.0, "threshold": 80},
                "architecture_constraints": {"score": 100.0, "threshold": 90},
            },
        }
        path.write_text(json.dumps(original))

        _call_finalize(monkeypatch, tmp_path, fr_id="FR-04", force=True)

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk != original, (
            "--force must allow overwriting a previously PASSing per-FR file"
        )

    def test_failing_per_fr_file_is_overwritten_normally(self, monkeypatch, tmp_path):
        # A per-FR file that does NOT yet represent a completed PASS (e.g.
        # a previous gate run that failed and needs to be re-run) MUST
        # still be overwritten normally — the guard only fires on
        # quality_complete=True && verdict=PASS.
        path = _per_fr_path(tmp_path, "FR-04")
        path.parent.mkdir(parents=True, exist_ok=True)
        failing = {
            "gate": 1, "phase": 3, "fr_id": "FR-04",
            "score": 60.0, "quality_complete": False,
            "verdict": "FAIL", "passed": False,
            "composite_score": 60.0,
            "breakdown": {
                "linting": {"score": 70.0, "threshold": 90},
                "type_safety": {"score": 60.0, "threshold": 85},
                "test_coverage": {"score": 50.0, "threshold": 80},
                "architecture_constraints": {"score": 60.0, "threshold": 90},
            },
        }
        path.write_text(json.dumps(failing))

        _call_finalize(monkeypatch, tmp_path, fr_id="FR-04")

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk != failing, (
            "a per-FR file with quality_complete=False must be overwritten "
            "by a subsequent PASS — the idempotency guard should only fire "
            "on quality_complete=True AND verdict=PASS"
        )
        # The new content must reflect the new gate run.
        assert on_disk["quality_complete"] is True


class TestGate1PromptEnumeratesAllDimensions:
    """Bug A invariant: the GATE1 prompt template enumerates all four
    dimensions the gate config declares, so the agent cannot accidentally
    omit architecture_constraints (the dimension that lacked tool_evidence
    in the P7 FR-09 block)."""

    def test_prompt_mentions_architecture_constraints_in_step_two(self):
        from cli.fr_prompts.gate import build_gate1_prompt
        prompt = build_gate1_prompt(
            "FR-09", 3, Path("/tmp/dummy"), Path("/tmp/dummy/srs.md"),
            "test_fr09.py",
        )
        assert "architecture_constraints" in prompt, (
            "GATE1 prompt must enumerate architecture_constraints in step 2 "
            "(the tool-execution checklist) so the agent runs lint-imports "
            "and captures its output as tool_evidence"
        )

    def test_prompt_schema_includes_architecture_constraints(self):
        from cli.fr_prompts.gate import build_gate1_prompt
        prompt = build_gate1_prompt(
            "FR-09", 3, Path("/tmp/dummy"), Path("/tmp/dummy/srs.md"),
            "test_fr09.py",
        )
        # The schema block is the JSON template the agent must mirror.
        assert '"architecture_constraints"' in prompt, (
            "GATE1 prompt schema must include the architecture_constraints "
            "key so the agent's gate1_result.json has a slot for it. The "
            "P7 FR-09 block was triggered by its absence."
        )

    def test_prompt_overall_score_uses_four_dimensions(self):
        from cli.fr_prompts.gate import build_gate1_prompt
        prompt = build_gate1_prompt(
            "FR-09", 3, Path("/tmp/dummy"), Path("/tmp/dummy/srs.md"),
            "test_fr09.py",
        )
        # Sanity: the formula should reference architecture_constraints
        # along with the three dimensions it always referenced.
        assert "architecture_constraints.score" in prompt, (
            "GATE1 prompt overall_score formula must include "
            "architecture_constraints.score — otherwise the agent computes "
            "the score from only 3 dimensions and the gate's weight math "
            "is silently wrong"
        )

"""
Regression tests for 4 remaining HIGH bugs in harness_bridge:

  1. _load_manifest_sab (line 1481) — bare `except Exception: return {}`
     silently disables ALL SAB-derived gate-score overrides when the
     manifest has any parse/IO problem. A truncated JSON turns strict
     SAB enforcement into silent default behavior with zero forensic
     trail.

  2. finalize_gate (line 1844) — `raw = json.loads(result_path.read_text(...))`
     has no try/except. A truncated `gate1_result.json` raises
     uncaught JSONDecodeError, bypassing the GateBlockedError contract.

  3. finalize_gate CRG enrichment (line 2117) — exception is only
     printed to stderr, not logged via the module logger. A real
     MCP enrichment failure leaves no audit trail in the decision
     log and silently drops the test_coverage hub-penalty fabrication
     signal.

  4. finalize_gate (line 2220) — for Gate 1, `_gate_passes` only
     verifies per-dim thresholds, missing the
     `result.score >= score_gate` check. The next line then
     unconditionally flips `quality_complete=True` whenever
     `_gate_passes` is True, silently bypassing the verdict block.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.harness_bridge import (
    GateBlockedError,
    GateContext,
    HarnessBridge,
)


@pytest.fixture
def bridge() -> HarnessBridge:
    return HarnessBridge()


@pytest.fixture
def gate1_ctx(tmp_path: Path) -> GateContext:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    return GateContext(
        gate_num=1,
        config={
            "score_gate": 75.0,
            "dimensions": [
                {"name": "linting", "threshold": 90.0, "weight": 0.5,
                 "tool": "ruff", "requires_tool_execution": False},
            ],
        },
        project_root=str(project_root),
        phase=3,
        fr_id="FR-001",
        ssi_scripts_dir="/dev/null",
        ssi_prompts_dir="/dev/null",
        ssi_schemas_dir="/dev/null",
        work_dir=str(work_dir),
    )


@pytest.fixture
def gate3_ctx(tmp_path: Path) -> GateContext:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    return GateContext(
        gate_num=3,
        config={
            "score_gate": 75.0,
            "dimensions": [
                {"name": "linting", "threshold": 90.0, "weight": 0.5,
                 "tool": "ruff", "requires_tool_execution": False},
            ],
        },
        project_root=str(project_root),
        phase=4,
        fr_id="FR-001",
        ssi_scripts_dir="/dev/null",
        ssi_prompts_dir="/dev/null",
        ssi_schemas_dir="/dev/null",
        work_dir=str(work_dir),
    )


def _write_gate_result(work_dir: Path, *, overall: float, dims: dict) -> None:
    """Write a gate{N}_result.json with the given scores."""
    (work_dir / "gate1_result.json").write_text(
        json.dumps({
            "overall_score": overall,
            "breakdown": {
                name: {"score": score, "threshold": threshold}
                for name, (score, threshold) in dims.items()
            },
        }, indent=2),
        encoding="utf-8",
    )


# ── Bug 1: bare except in _load_manifest_sab ─────────────────────────────────

class TestSabManifestSilentDisable:
    def test_manifest_parse_error_logs_warning(
        self, bridge: HarnessBridge, tmp_path: Path, caplog,
    ):
        """When the quality_manifest.json is malformed JSON, the
        bare `except Exception: return {}` silently disables all
        SAB-derived gate_score_overrides. The fix must (a) narrow
        the catch and (b) log a WARNING so the operator sees
        that SAB enforcement is offline."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / ".methodology").mkdir()
        # Write a corrupt manifest
        (project_root / ".methodology" / "quality_manifest.json").write_text(
            "{ corrupted json", encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING, logger="harness.harness_bridge"):
            result = bridge._load_manifest_sab(str(project_root))
        # The silent `return {}` is the bug; the contract is that
        # the operator must be warned. Fix: WARNING log entry.
        assert any(
            "sab" in rec.message.lower() or "manifest" in rec.message.lower()
            for rec in caplog.records
        ), (
            f"SAB manifest parse error must produce a WARNING log; "
            f"got: {[(r.levelname, r.message) for r in caplog.records]}"
        )
        # And the return value is still {} (pipeline keeps working).
        assert result == {}


# ── Bug 2: unwrapped json.loads in finalize_gate ─────────────────────────────

class TestFinalizeGateJsonDecodeError:
    def test_corrupt_gate_result_raises_gate_blocked_error(
        self, gate1_ctx: GateContext, caplog,
    ):
        """A truncated/corrupt gate1_result.json must surface as
        GateBlockedError (per the docstring contract), not as an
        uncaught JSONDecodeError that bypasses the gate machinery."""
        # Write a truncated JSON
        (Path(gate1_ctx.work_dir) / "gate1_result.json").write_text(
            '{"breakdown": {', encoding="utf-8"
        )
        bridge = HarnessBridge()
        # The fix: json.JSONDecodeError → GateBlockedError. Currently
        # the JSONDecodeError propagates uncaught.
        with pytest.raises(GateBlockedError):
            bridge.finalize_gate(gate1_ctx)


# ── Bug 3: CRG enrichment silent drop ───────────────────────────────────────

class TestCrgEnrichmentSilentDrop:
    def test_crg_enrichment_exception_logs_warning(
        self, gate3_ctx: GateContext, caplog, monkeypatch,
    ):
        """When _crg_enrich_gate_findings raises (MCP failure,
        import error, etc.), the finalize_gate handler currently
        only prints to stderr. The fix must log via the module
        logger so a real CRG failure is in the decision log /
        forensic trail. The gate may still complete (the bug is
        the silent drop of the hub-penalty signal, not a hard
        raise) — what we require is a WARNING log entry.

        Uses Gate 3 (gate_num >= 2) because the CRG enrichment
        code path is only exercised for non-Gate-1 gates."""
        # ── Round 45: monkeypatch gate_config_path to avoid loading the
        # LIVE gate3_p4_exit.yaml which has 14 requires_tool_execution:true
        # dimensions. The test's gate3_result.json only has one dim entry
        # (linting), so _check_tool_evidence would block with violations
        # before the CRG enrichment code is ever reached. Return a minimal
        # config matching the test's gate3_result.json shape.
        import core.quality_gate.gate_thresholds as _gt
        _cfg_path = Path(gate3_ctx.work_dir) / "gate3_p4_exit.yaml"
        _cfg_path.write_text(
            "gate_num: 3\n"
            "score_gate: 75.0\n"
            "dimensions:\n"
            "  - { name: linting, tier: 1, threshold: 90, weight: 0.10,\n"
            "      tool: ruff,  requires_tool_execution: false }\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: _cfg_path)

        # Write a valid gate3 result so finalize_gate gets past the
        # JSON-read step. Round 21 站2: "valid" now means it satisfies
        # harness_gate_result.schema.json — this fixture called itself valid
        # while omitting three required fields, which nothing could tell it
        # until the schema became executable.
        (Path(gate3_ctx.work_dir) / "gate3_result.json").write_text(
            json.dumps({
                "overall_score": 85.0,
                "quality_complete": True,
                "open_critical_count": 0,
                "open_high_count": 0,
                "breakdown": {"linting": {"score": 90.0, "threshold": 90.0}},
            }, indent=2),
            encoding="utf-8",
        )
        bridge = HarnessBridge()

        with patch(
            "harness.harness_bridge._crg_enrich_gate_findings",
            side_effect=RuntimeError("simulated CRG MCP failure"),
        ):
            with caplog.at_level(logging.WARNING, logger="harness.harness_bridge"):
                # The gate may succeed or fail; we don't care — the
                # contract is that the enrichment failure is LOGGED.
                try:
                    bridge.finalize_gate(gate3_ctx)
                except GateBlockedError:
                    pass  # acceptable — what matters is the log

        # The enrichment failure must be logged via the module
        # logger, not just printed to stderr.
        assert any(
            "crg" in rec.message.lower() and "enrich" in rec.message.lower()
            for rec in caplog.records
        ), (
            f"CRG enrichment failure must produce a WARNING log entry; "
            f"got: {[(r.levelname, r.message) for r in caplog.records]}"
        )


# ── Bug 4: Gate 1 flip when overall_score < score_gate ──────────────────────

class TestGate1ScoreGateCheck:
    def test_gate1_overall_below_score_gate_raises_blocked(
        self, gate1_ctx: GateContext,
    ):
        """For Gate 1, the current `_gate_passes` only checks per-dim
        thresholds, missing the `result.score >= score_gate` check.
        An FR with all dims passing but overall_score < score_gate
        gets silently flipped to PASS. Fix: also require
        result.score >= score_gate for Gate 1."""
        # All dims pass their thresholds, but overall_score (50.0)
        # is below score_gate (75.0). After the fix, this must
        # raise GateBlockedError, not flip to PASS.
        _write_gate_result(
            Path(gate1_ctx.work_dir),
            overall=50.0,
            dims={"linting": (90.0, 90.0)},  # all pass per-dim
        )
        bridge = HarnessBridge()
        with pytest.raises(GateBlockedError):
            bridge.finalize_gate(gate1_ctx)

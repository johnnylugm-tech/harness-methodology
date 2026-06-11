"""
Regression tests for 2 LOW bugs in harness_bridge:

  1. finalize_gate (line 2146) — variance check has bare
     `except Exception: pass` (with pylint disable comment that
     acknowledges the suppression). Real errors during the
     variance check (ImportError, TypeError, AttributeError) are
     silently swallowed. Fix: narrow the except and log a WARNING
     so the suppressed failure is at least visible in logs.

  2. _load_config (line 2528) — `names = {1: ..., 2: ..., 3: ..., 4: ...}`
     with caller-supplied gate_num. An out-of-range gate_num
     (e.g. 5) raises uncaught KeyError. Fix: validate gate_num
     is in 1-4 and raise ValueError with a clear message.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.harness_bridge import HarnessBridge, GateContext


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    return GateContext(
        gate_num=3,
        config={},
        project_root=str(tmp_path),
        phase=4,
        fr_id="FR-001",
        ssi_scripts_dir="/dev/null",
        ssi_prompts_dir="/dev/null",
        ssi_schemas_dir="/dev/null",
        work_dir=str(tmp_path / "work"),
    )


# ── Bug 1: variance check exception swallowing ──────────────────────────────

class TestVarianceCheckExceptionSurfaced:
    def test_variance_check_runtime_error_logs_warning(
        self, ctx: GateContext, caplog,
    ):
        """If the per-dim variance check itself raises (e.g.
        statistics.UndefinedError for n=1, or some downstream
        side-effect failure), the current code swallows it via
        `except Exception: pass`. The fix must log a WARNING so
        the suppressed failure is visible in forensic review."""
        bridge = HarnessBridge()
        dims = []  # Empty — variance check skips (n < 3) without error.
        # Inject a runtime error by patching statistics.pstdev to
        # raise something not in the (n<3) skip path. The cleanest
        # way is to make len(dims) >= 3 but force pstdev to raise.
        from harness.harness_bridge import DimResult
        dims = [
            DimResult(name="d1", score=80.0, threshold=80.0),
            DimResult(name="d2", score=80.0, threshold=80.0),
            DimResult(name="d3", score=80.0, threshold=80.0),
        ]
        with patch("statistics.pstdev", side_effect=RuntimeError("simulated")):
            with caplog.at_level(logging.WARNING, logger="harness.harness_bridge"):
                # Must NOT raise; must log a WARNING instead.
                bridge._variance_check_log(ctx, dims)
        # A WARNING about the variance-check failure must be logged.
        assert any(
            "variance" in rec.message.lower() for rec in caplog.records
        ), (
            f"variance-check failure must produce a WARNING log entry; "
            f"got: {[(r.levelname, r.message) for r in caplog.records]}"
        )


# ── Bug 2: _load_config out-of-range gate_num ───────────────────────────────

class TestLoadConfigGateNumValidation:
    def test_out_of_range_gate_num_raises_value_error(self):
        bridge = HarnessBridge()
        with pytest.raises(ValueError, match="gate_num"):
            bridge._load_config(gate_num=99)
        with pytest.raises(ValueError, match="gate_num"):
            bridge._load_config(gate_num=0)
        with pytest.raises(ValueError, match="gate_num"):
            bridge._load_config(gate_num=-1)

    def test_valid_gate_num_range_still_works(self):
        """Sanity guard: gate_num 1-4 must continue to work."""
        bridge = HarnessBridge()
        for g in (1, 2, 3, 4):
            cfg = bridge._load_config(gate_num=g)
            assert cfg.gate_num == g

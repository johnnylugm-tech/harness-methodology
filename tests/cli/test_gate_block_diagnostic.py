"""Round 24 站1 — the agent-facing gate-BLOCKED diagnostic must carry a cause.

`cli/gate_cmds.py::_format_block_diagnostic` writes both the stdout diagnostic
and `.methodology/last_block.md`. It had **zero test coverage** before this
file, which is how it shipped modelling exactly one of the ten ways
`finalize_gate` can block.

The regression fixture is the real artifact from the run-all-by-workflow
P1-P8 validation run (`.methodology/last_block.md`, Phase 8 / FR-05):

    fr_id: FR-05 | rounds: 0 | open_critical: 1 | open_high: 0
    ## Failing Dimensions          <- empty
    ## Resume Commands             <- "re-run run-gate", nothing else

Every dimension passed; the gate blocked on an open CRITICAL finding; the
diagnostic named nothing and the only action offered was to run the gate
again. Five seconds later the same FR scored 100.0.
"""

from __future__ import annotations

import pytest

from cli.gate_cmds import _format_block_diagnostic
from harness.harness_bridge import DimResult, GateBlockedError, GateResult

pytestmark = [pytest.mark.core]


def _diag(tmp_path, *, dimensions, details=None, open_critical=0, open_high=0,
          score=97.4, gate_num=4, phase=6, fr_id=None):
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    exc = GateBlockedError(
        gate_num,
        GateResult(
            gate_num=gate_num, score=score, dimensions=list(dimensions),
            open_critical=open_critical, open_high=open_high,
            quality_complete=False, rounds_used=1,
        ),
        details,
    )
    text = _format_block_diagnostic(exc, gate_num, phase, fr_id, 3, tmp_path)
    report = (tmp_path / ".methodology" / "last_block.md").read_text(encoding="utf-8")
    return text, report


_PASSING = [DimResult(name="linting", score=100.0, threshold=100.0)]


def test_open_critical_only_block_names_its_cause(tmp_path):
    """The run-all-by-workflow shape — the whole reason this suite exists."""
    text, report = _diag(
        tmp_path, dimensions=_PASSING, open_critical=1, score=100.0,
        gate_num=1, phase=8, fr_id="FR-05",
    )
    for out in (text, report):
        assert "open_critical_findings" in out
        assert "1 unresolved CRITICAL" in out
        assert "gate1_result.json" in out
    # The empty-section shape must be gone from the persisted report.
    assert "## Blocking Reasons (1)" in report
    assert "## Failing Dimensions" not in report


def test_fabrication_block_reaches_the_agent_verbatim(tmp_path):
    """A tool_score_fabrication block previously rendered as an empty list."""
    text, report = _diag(
        tmp_path, dimensions=_PASSING, open_critical=1, gate_num=1, fr_id="FR-01",
        details={"tool_score_fabrication": ["security: agent 95.0 vs harness 40.0"]},
    )
    for out in (text, report):
        assert "tool_score_fabrication" in out
        assert "security: agent 95.0 vs harness 40.0" in out
        assert "Do NOT re-run the gate" in out


def test_failing_dimension_still_renders_its_hint(tmp_path):
    text, report = _diag(
        tmp_path,
        dimensions=[DimResult(name="test_coverage", score=67.0, threshold=80.0)],
        score=67.0, gate_num=1,
    )
    for out in (text, report):
        assert "test_coverage" in out
        assert "pytest --cov" in out


def test_details_and_dimensions_both_reported(tmp_path):
    text, _ = _diag(
        tmp_path,
        dimensions=[DimResult(name="linting", score=95.0, threshold=100.0)],
        details={"infra_fail": ["mutmut: command not found"]},
        gate_num=1, score=95.0,
    )
    assert "Blocking reasons (2)" in text
    assert "infra_fail" in text and "linting" in text
    # Ordering: the specific cause comes before the generic dimension gap.
    assert text.index("infra_fail") < text.index("scored 95.0")


@pytest.mark.parametrize("kwargs", [
    {"dimensions": _PASSING, "open_critical": 1, "score": 100.0},
    {"dimensions": _PASSING, "open_high": 2, "score": 100.0},
    {"dimensions": _PASSING, "score": 61.0},
    {"dimensions": [DimResult(name="linting", score=1.0, threshold=100.0)], "score": 1.0},
    {"dimensions": _PASSING, "details": {"da_waiver": ["premise false"]}, "score": 100.0},
    {"dimensions": _PASSING, "details": {"brand_new": ["x"]}, "score": 100.0},
])
def test_no_blocked_output_is_ever_an_empty_list_plus_a_bare_rerun(tmp_path, kwargs):
    """The contract: whatever blocked it, the agent gets a named cause.

    A diagnostic whose only content is "0 reasons" and "re-run the gate" is
    exactly what let a fabrication block get cleared by re-rolling the dice.
    """
    text, report = _diag(tmp_path, **kwargs)
    for out in (text, report):
        assert "reasons (0)" not in out.lower()
        assert "Blocking Reasons (0)" not in out
    # Persisted report must carry at least one `- fix:` line with real content.
    fixes = [ln for ln in report.splitlines() if ln.startswith("- fix:")]
    assert fixes, f"last_block.md carries no remediation:\n{report}"
    assert all(len(ln) > len("- fix: ") + 40 for ln in fixes), fixes


def test_null_scored_dimension_never_renders_as_a_failure(tmp_path):
    """5467049 / 68209a9 null-score contract, at the diagnostic layer."""
    _, report = _diag(
        tmp_path,
        dimensions=[
            DimResult(name="mutation_testing", score=None, threshold=70.0),
            DimResult(name="linting", score=100.0, threshold=100.0),
        ],
        open_critical=1, score=100.0, gate_num=1,
    )
    assert "mutation_testing" not in report

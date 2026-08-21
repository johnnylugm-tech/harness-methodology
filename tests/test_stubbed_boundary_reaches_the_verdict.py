"""Round 67 站0 — a dimension the framework did not measure cannot pass.

Round 51 站3 built `_mark_stubbed_boundary_dimensions`: when an autouse fixture
replaces a SAB high-risk module, the suite-measured dimensions get
`score_source = stubbed_boundary`, and its docstring says the marker means
"the composite stops claiming to cover weight it did not measure".

Measured on taskq-cc's committed Gate 4 (2026-08-21), that is not what happens:

    "composite_score": 95.28
    "measurement_scope": {"weight_covered": 0.88,
                          "dimensions_unscored": ["integration_coverage",
                                                  "test_coverage"]}
    "breakdown": {"test_coverage": {"score": 100.0, ...},
                  "integration_coverage": {"score": 82.0, ...}}

Recomputing from `gate4_p6_full.yaml`'s weights: over ALL dimensions (weight
1.0) the composite is 95.28 — the committed value, to the last digit. Over the
0.88 the same file publishes it is 95.6591. So the denominator beside the
number is not the denominator that produced it, and both propositions are in
one artifact.

The five findings behind that 0.88 were five autouse fixtures replacing
`taskq_api.service.auth` across five FR test files. `test_coverage` scored
100.0 and the gate published PASS.

Two assertions, one rule: a score the framework did not measure over the
delivered code is not evidence that dimension passed, and the composite's
denominator is the one the verdict used.

Note on the alternative that was rejected: dropping those weights from the
composite (making 0.88 true) RAISES the score here, because the dimension it
drops is the low one. "The tests replaced the thing they measure" must not be
worth points.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from harness_cli import cmd_finalize_gate

_HIGH_RISK = "shopfront.service.auth"

_SUITE_WITH_A_STUBBED_BOUNDARY = '''
import pytest


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    monkeypatch.setattr("shopfront.service.auth.verify", lambda *a: True)
    yield
'''


def _finalize_with_stubbed_boundary(monkeypatch, tmp_path: Path, *, phase=3):
    """Run a real Gate 1 finalize over a tree whose suite stubs its own
    high-risk boundary. Returns (exit_code, stdout)."""
    sessi = tmp_path / ".sessi-work"
    (sessi / "sentinels").mkdir(parents=True, exist_ok=True)
    (sessi / "gate1_result.json").write_text(json.dumps({
        "gate": 1, "phase": phase, "fr_id": "FR-01",
        "score": 95.0, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": {
            "linting": {"score": 100.0, "threshold": 90},
            "type_safety": {"score": 98.5, "threshold": 85},
            # The one the stub reaches. High enough that nothing else can
            # explain a block.
            "test_coverage": {"score": 100.0, "threshold": 80},
            "architecture_constraints": {"score": 100.0, "threshold": 90},
        },
    }))
    (sessi / "sentinels" / f"g1_p{phase}_fr01.flag").write_text("test")

    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}))
    (meth / "state.json").write_text(
        json.dumps({"state": "ACTIVE", "current_phase": phase}))
    # The SAB is what makes a module high-risk; without it the scan abstains
    # rather than passing (boundary_realism's own rule).
    (meth / "SAB.json").write_text(json.dumps({"high_risk_modules": [_HIGH_RISK]}))

    # A root conftest rather than a test file under a tests/ directory:
    # `stubbed_attributes` scans both (it walks the tree for conftest.py and
    # test_*.py alike), and materialising a tests/ directory here makes the
    # TDD pre-checks demand a committed FR test file — a real rule, but not
    # the one under test, and it blocks before the boundary scan runs.
    (tmp_path / "conftest.py").write_text(_SUITE_WITH_A_STUBBED_BOUNDARY)

    import core.quality_gate.gate_thresholds as _gt
    import yaml as _yaml
    cfg = tmp_path / "gate1_minimal.yaml"
    cfg.write_text(_yaml.dump({
        "gate": 1,
        "dimensions": [
            {"name": "linting", "threshold": 90, "weight": 0.25},
            {"name": "type_safety", "threshold": 85, "weight": 0.25},
            {"name": "test_coverage", "threshold": 80, "weight": 0.25},
            {"name": "architecture_constraints", "threshold": 90, "weight": 0.25},
        ],
    }))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg)

    class Args:
        pass
    a = Args()
    a.gate = 1  # type: ignore[attr-defined]
    a.phase = phase  # type: ignore[attr-defined]
    a.project = str(tmp_path)  # type: ignore[attr-defined]
    a.fr_id = "FR-01"  # type: ignore[attr-defined]
    a.force = False  # type: ignore[attr-defined]
    # The public seam for "this invocation may not touch git" (cli._shared
    # .git_enabled), rather than patching `_make_git` — tests/test_patch_
    # discipline.py refuses a private-name patch where a public knob exists.
    a.no_git = True  # type: ignore[attr-defined]

    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    try:
        code = cmd_finalize_gate(a)  # type: ignore[arg-type]
    except SystemExit as exc:
        code = exc.code
    return code, captured.getvalue()


def test_a_stubbed_boundary_dimension_does_not_pass(monkeypatch, tmp_path):
    """The verdict reads the same marker `measurement_scope` reads.

    `_SOURCES_NOT_FRAMEWORK_MEASURED`'s own comment says it exists so two
    readers cannot drift apart. It had two readers, and neither of them was
    the verdict.
    """
    code, out = _finalize_with_stubbed_boundary(monkeypatch, tmp_path)

    assert code != 0, (
        "Gate 1 passed with test_coverage measured over a suite that replaces "
        f"{_HIGH_RISK}. The framework detected the replacement (it is in the "
        "degradation ledger and in measurement_scope) and then scored the "
        "dimension 100.0 and let it through"
    )
    assert _HIGH_RISK in out and "_mock_auth" in out, (
        "the block has to name what was replaced and what replaced it — "
        "'a dimension was not measured' is not something a project can act "
        f"on. Got:\n{out[-1500:]}"
    )


def test_the_composite_denominator_has_one_definition():
    """One artifact, one denominator.

    taskq-cc's Gate 4 published `weight_covered: 0.88` beside a number
    averaged over 1.0. The two came from two places: `measurement_scope`
    selects on `score_source`, and finalize_gate's own averaging loop is an
    inline `for d in dims: if d.score is None: continue` that has never heard
    of it.

    The fix is not to teach the loop the same rule — that is the second copy
    Round 33 keeps finding. It is for both to ask one function. So this pins
    the function into existence: whatever `composite_over` averages, its
    weight must BE `measurement_scope`'s `weight_covered`, not a number that
    agrees with it today.
    """
    from harness.harness_bridge import (
        DimResult, SCORE_SOURCE_STUBBED_BOUNDARY, composite_over,
        measurement_scope,
    )

    weights = {"linting": 0.5, "test_coverage": 0.5}
    dims = [
        DimResult(name="linting", score=100.0, threshold=90.0, issues=[]),
        DimResult(name="test_coverage", score=100.0, threshold=80.0, issues=[],
                  score_source=SCORE_SOURCE_STUBBED_BOUNDARY),
    ]

    scope = measurement_scope(dims, weights)
    composite = composite_over(dims, weights)

    assert composite["weight"] == scope["weight_covered"], (
        f"the composite was averaged over {composite['weight']} and the "
        f"artifact beside it says {scope['weight_covered']}. Two derivations "
        f"of one denominator is two denominators"
    )
    assert composite["dimensions"] == scope["dimensions_scored"], (
        "the composite and the scope disagree about which dimensions were "
        f"averaged: {composite['dimensions']} vs {scope['dimensions_scored']}"
    )

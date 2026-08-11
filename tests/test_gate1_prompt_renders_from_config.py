"""The GATE1 prompt must not hold its own copy of the gate's dimension list.

Round 45 站0. On 2026-08-11 a live P7 run blocked FR-09 with a false positive.
`b288c9d`'s own commit message names the cause:

    The GATE1 prompt template (cli/fr_prompts/gate.py) listed only 3 of the
    4 dimensions declared in gate1_per_fr.yaml, omitting
    architecture_constraints. Agents that read the template verbatim produced
    a gate1_result.json whose architecture_constraints block lacked
    tool_evidence, tripping the S3 evidence check.

The fix that shipped typed the fourth dimension into the template and pinned
it in `test_prompt_gate_parity.py`. That is the statement layer: the prompt
still restates what `harness/gate_configs/gate1_per_fr.yaml` declares, so the
fifth dimension will drift exactly the way the fourth did. Round 17 built the
prompt↔gate parity registry for this shape and it covered thresholds, not the
dimension SET or the weights.

`core/quality_gate/gate_thresholds.py::load_gate_dimensions` already returns
the entries in YAML order for exactly this kind of render (Round 39 站3).
Three places in the prompt have to come from it: the step-2 tool checklist,
the step-3 schema template, and the `overall_score` formula.

Deliberately still prose: the per-dimension scoring formula
("ruff exit 0 → 100") is not in the YAML and does not belong there. It stays
a declared mapping — and a dimension with no entry in it raises at build time,
so adding one to the YAML cannot silently produce a prompt that says nothing
about how to score it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8")
    (tmp_path / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    return tmp_path


def _use_gate1_yaml(tmp_path: Path, dims: list[dict], monkeypatch) -> None:
    import yaml

    import core.quality_gate.gate_thresholds as _gt
    cfg = tmp_path / "gate1_per_fr.yaml"
    cfg.write_text(yaml.dump({"gate": 1, "dimensions": dims}), encoding="utf-8")
    _real = _gt.gate_config_path
    monkeypatch.setattr(
        _gt, "gate_config_path", lambda g: cfg if g == 1 else _real(g))
    # The read is lru_cached; the autouse fixture clears it either side of the
    # test, and this clears the entry the fixture setup itself may have warmed.
    _gt._read_gate_config.cache_clear()


@pytest.fixture(autouse=True)
def _clear_config_cache():
    import core.quality_gate.gate_thresholds as _gt
    _gt._read_gate_config.cache_clear()
    yield
    _gt._read_gate_config.cache_clear()


_FOUR = [
    {"name": "linting", "tool": "ruff", "threshold": 100,
     "weight": 0.25, "requires_tool_execution": True},
    {"name": "type_safety", "tool": "pyright", "threshold": 100,
     "weight": 0.25, "requires_tool_execution": True},
    {"name": "test_coverage", "tool": "pytest-cov", "threshold": 80,
     "weight": 0.25, "requires_tool_execution": True},
    {"name": "architecture_constraints", "tool": "import-linter",
     "threshold": 100, "weight": 0.25, "requires_tool_execution": True},
]


def _build(project: Path) -> str:
    from cli.fr_prompts.gate import build_gate1_prompt
    return build_gate1_prompt(
        "FR-01", 3, project, project / "SRS.md", "tests/test_fr01.py",
    )


def test_a_fifth_dimension_reaches_all_three_places(project, monkeypatch):
    """The `b288c9d` failure mode, one dimension later."""
    from cli.fr_prompts import gate as gate_prompts

    dims = [dict(d, weight=0.2) for d in _FOUR] + [
        {"name": "error_handling", "tool": "ast-error-handling",
         "threshold": 80, "weight": 0.2, "requires_tool_execution": True},
    ]
    _use_gate1_yaml(project, dims, monkeypatch)
    monkeypatch.setitem(
        gate_prompts.GATE1_SCORING_PROSE, "error_handling",
        "ast-error-handling scan: with_handler / total × 100",
    )

    text = _build(project)

    assert text.count("error_handling") >= 3, (
        "the fifth dimension must appear in the tool checklist, the schema "
        "template and the overall_score formula"
    )
    assert "error_handling.score × 0.2" in text


def test_the_formula_uses_the_weights_the_yaml_declares(project, monkeypatch):
    """`b288c9d` also had to hand-correct 0.33/0.33/0.34 to 0.25×4."""
    dims = [
        dict(_FOUR[0], weight=0.4), dict(_FOUR[1], weight=0.3),
        dict(_FOUR[2], weight=0.2), dict(_FOUR[3], weight=0.1),
    ]
    _use_gate1_yaml(project, dims, monkeypatch)

    text = _build(project)

    assert "linting.score × 0.4" in text
    assert "type_safety.score × 0.3" in text
    assert "test_coverage.score × 0.2" in text
    assert "architecture_constraints.score × 0.1" in text


def test_every_threshold_comes_from_the_same_place(project, monkeypatch):
    """Three of the four already read the YAML; `architecture_constraints`
    was a literal `100` in the schema template."""
    dims = [
        dict(_FOUR[0], threshold=91), dict(_FOUR[1], threshold=92),
        dict(_FOUR[2], threshold=93), dict(_FOUR[3], threshold=94),
    ]
    _use_gate1_yaml(project, dims, monkeypatch)

    text = _build(project)

    for expected in ("91", "92", "93", "94"):
        assert f'"threshold": {expected}' in text, expected


def test_a_dimension_with_no_scoring_prose_is_a_build_time_failure(
    project, monkeypatch,
):
    """The one part that is deliberately not in the YAML must still be forced.
    Silence here is how a prompt ends up telling an agent to score something
    without saying how."""
    dims = [dict(d, weight=0.2) for d in _FOUR] + [
        {"name": "readability", "tool": "readability-v2", "threshold": 80,
         "weight": 0.2, "requires_tool_execution": True},
    ]
    _use_gate1_yaml(project, dims, monkeypatch)

    with pytest.raises(KeyError, match="readability"):
        _build(project)


def test_the_shipped_config_still_renders_four_dimensions(project):
    """Anchor on the real YAML — the render must not change today's prompt's
    subject matter, only where it gets it from."""
    text = _build(project)

    for name in ("linting", "type_safety", "test_coverage",
                 "architecture_constraints"):
        assert name in text, name
    assert "× 0.25" in text

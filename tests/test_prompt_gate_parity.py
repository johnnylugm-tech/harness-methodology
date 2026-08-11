"""Round 17 站1 (finding A) — declarative prompt↔gate parity registry.

The GATE1 dispatch prompt tells each sub-agent what Gate 1 will enforce.
Whenever a rule is ALSO hand-written in the prompt there are two copies of
one truth; drift bakes a wrong number into the prompt as a hard instruction,
and N fresh sub-agents deterministically reproduce it. This exact class hit
production four times before any structural guard existed:
  #15 pragma allowlist / #16 spec-cap scan / #18B pragma example /
  #20 spec parser (FR-05 ran 8 GATE1 rounds on a number that was wrong).

Those four were each fixed per-site. This registry is the STRUCTURAL close:
every gate rule the prompt states is bound to its authoritative SSOT, and a
completeness meta-test forces the NEXT hand-copied threshold to fail loudly
(test_no_unbound_hardcoded_threshold_in_prompt) instead of silently drifting.

Same declarative-registry + completeness-meta-test shape as
tests/test_workflow_dispatch_registry.py (DISPATCH_REGISTRY) and Round 16's
failure-mode registry.

Deliberately NOT unified here (documented divergence, not an oversight):
the GATE1 prompt's overall_score weights (0.33/0.33/0.34, 3 dims) vs
gate1_per_fr.yaml's 0.25×4 (incl. architecture_constraints the agent never
scores). gate1 has no CRG override, so harness_bridge.py:2399 adopts the
agent's reported overall_score — the prompt's weights are the de-facto
authority and the YAML weight is dead config for gate1. Unifying them touches
the gate2/3/4 fallback path AND the agent output contract, out of station-1
scope. test_overall_score_weight_asymmetry_is_pinned records the current
state so any change is deliberate; re-open when overall_score's dim set is
adjudicated.

Resolution (2026-08-11, Bug A fix for P7 FR-09 false-positive block):
The 3-dim / 4-dim divergence was the prompt-side cause of the P7 FR-09
escalation — the agent template omitted the architecture_constraints
dimension, so even agents that filled out the 3 dims got a sub-1.0
overall_score due to the missing 4th dim. The prompt's overall_score
formula now matches the YAML's 0.25×4. test_overall_score_weight_
asymmetry_is_pinned is updated to pin the new unified state — the
deliberate change register this test exists to enforce is now closed.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import yaml  # type: ignore[import-untyped]

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.fr_cmds import (  # noqa: E402
    _build_fr_step_prompt,
    _extract_test_spec_names,
)
from core.quality_gate.sab_parser import _GATE1_DIMENSION_STANDARD  # noqa: E402

import pytest  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_GATE1_YAML = REPO / "harness" / "gate_configs" / "gate1_per_fr.yaml"


def _min_project(tmp_path: Path) -> Path:
    """Minimal project layout letting GATE1 / COVERAGE-FIX render to completion."""
    for sub in ("03-development/tests", "03-development/src",
                "02-architecture", "01-requirements", ".methodology"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "### FR-01: Widget\n\nMUST accept input.\n\n---\n", encoding="utf-8")
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        "### FR-01: Widget\n\n| # | Test Function | Type |\n"
        "|---|--------------|------|\n| 1 | test_fr01_01_a | Functional |\n",
        encoding="utf-8")
    (tmp_path / "03-development" / "tests" / "test_fr01.py").write_text(
        "def test_fr01_01_a():\n    assert True\n", encoding="utf-8")
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(
        '{"gate_score_overrides": {}}', encoding="utf-8")
    return tmp_path


def _render(step: str, tmp_path: Path) -> str:
    proj = _min_project(tmp_path)
    srs = proj / "01-requirements" / "SRS.md"
    return _build_fr_step_prompt(step, "FR-01", 3, proj, srs, tool_snapshot="X")


def _gate1_dim(field: str) -> dict[str, float]:
    raw = yaml.safe_load(_GATE1_YAML.read_text(encoding="utf-8"))
    return {d["name"]: d[field] for d in raw["dimensions"]}


# ── binding checks (each takes a rendered prompt, asserts the SSOT binding) ──

def _mk_threshold_check(dim: str) -> Callable[[str], None]:
    def _check(prompt: str) -> None:
        val = int(_GATE1_DIMENSION_STANDARD[dim])
        assert f'"threshold": {val}' in prompt, (
            f"{dim} threshold {val} (SSOT _GATE1_DIMENSION_STANDARD) not "
            f"rendered in GATE1 prompt — the prompt must source it, not "
            f"hand-copy it")
    return _check


def _check_pragma_allowlist(prompt: str) -> None:
    from core.phase_hooks import PRAGMA_NO_COVER_ALLOWLIST
    for pat in PRAGMA_NO_COVER_ALLOWLIST:
        assert pat in prompt, (
            f"pragma pattern {pat!r} from PRAGMA_NO_COVER_ALLOWLIST SSOT not "
            f"rendered verbatim in COVERAGE-FIX prompt (#18B binding)")


def _check_spec_parser(_prompt: str) -> None:
    # #20: _extract_test_spec_names delegates to the one canonical parser
    # (spec_coverage._parse_test_spec) finalize-gate's S4 check also uses.
    src = inspect.getsource(_extract_test_spec_names)
    assert "_parse_test_spec" in src, (
        "_extract_test_spec_names must delegate to the shared "
        "spec_coverage._parse_test_spec, not re-declare a second parser (#20)")


class PromptGateRule(NamedTuple):
    rule_id: str
    step: str            # dispatch step whose prompt carries this rule
    gate_ssot: str       # human-readable authority reference
    assert_bound: Callable[[str], None]


# Declarative SSOT: every gate rule the prompt states, bound to its authority.
# first-column rule_id set is pinned by the completeness meta-test below.
PROMPT_GATE_RULES: tuple[PromptGateRule, ...] = (
    PromptGateRule("threshold_linting", "GATE1",
                   "sab_parser._GATE1_DIMENSION_STANDARD['linting'] == gate1_per_fr.yaml",
                   _mk_threshold_check("linting")),
    PromptGateRule("threshold_type_safety", "GATE1",
                   "sab_parser._GATE1_DIMENSION_STANDARD['type_safety'] == gate1_per_fr.yaml",
                   _mk_threshold_check("type_safety")),
    PromptGateRule("threshold_test_coverage", "GATE1",
                   "sab_parser._GATE1_DIMENSION_STANDARD['test_coverage'] == gate1_per_fr.yaml",
                   _mk_threshold_check("test_coverage")),
    PromptGateRule("pragma_allowlist", "COVERAGE-FIX",
                   "core.phase_hooks.PRAGMA_NO_COVER_ALLOWLIST",
                   _check_pragma_allowlist),
    PromptGateRule("spec_parser", "GATE1",
                   "core.quality_gate.spec_coverage._parse_test_spec",
                   _check_spec_parser),
)

# Pinned separately (not derived from the registry) so adding a rule forces a
# deliberate edit here too — mirrors Round 16's failure-registry completeness.
_EXPECTED_RULE_IDS = {
    "threshold_linting", "threshold_type_safety", "threshold_test_coverage",
    "pragma_allowlist", "spec_parser",
}


@pytest.mark.parametrize("rule", PROMPT_GATE_RULES, ids=lambda r: r.rule_id)
def test_prompt_rule_is_bound_to_its_gate_ssot(rule, tmp_path):
    rule.assert_bound(_render(rule.step, tmp_path))


def test_registry_covers_exactly_the_expected_rule_ids():
    assert {r.rule_id for r in PROMPT_GATE_RULES} == _EXPECTED_RULE_IDS, (
        "PROMPT_GATE_RULES drifted from _EXPECTED_RULE_IDS. Adding a gate "
        "rule to the prompt requires registering its SSOT binding here AND "
        "updating _EXPECTED_RULE_IDS — that is the point.")


def test_gate1_yaml_thresholds_match_standard_ssot():
    """gate1_per_fr.yaml's tool-dim thresholds MUST equal the standard table.

    Round 45 站4 moved the PROMPT off `_GATE1_DIMENSION_STANDARD` and onto
    `load_gate_dimensions(1)` — the YAML itself — so this no longer legitimises
    the prompt. The table is still live for `core/quality_gate/sab_parser.py`,
    which merges it, so the two must still agree; a divergence would now
    mis-instruct the SAB path rather than the prompt.
    """
    thr = _gate1_dim("threshold")
    for name in ("linting", "type_safety", "test_coverage"):
        assert thr[name] == _GATE1_DIMENSION_STANDARD[name], (
            f"{name}: gate1_per_fr.yaml threshold {thr[name]} != standard "
            f"{_GATE1_DIMENSION_STANDARD[name]} — prompt sources the standard, "
            f"so a divergence here silently mis-instructs GATE1 sub-agents")


def test_no_unbound_hardcoded_threshold_in_prompt():
    """Completeness /母體封口: the GATE1 threshold floor must be sourced from
    the SSOT, never a re-introduced literal. A future `max(90.0, ...)` slip is
    exactly the finding-A drift class; this makes it fail at author time.

    Round 45 站4: the SSOT is now `load_gate_dimensions(1)` — gate1_per_fr.yaml
    read directly, one dimension at a time — rather than the derived
    `_GATE1_DIMENSION_STANDARD` table. That closes the half `b288c9d` could
    not: a threshold sourced from the table still left the dimension SET and
    the WEIGHTS as prose, and the set is what drifted.
    """
    from cli.fr_prompts.gate import _gate1_dimensions, build_gate1_prompt
    src = (inspect.getsource(_build_fr_step_prompt)
           + inspect.getsource(build_gate1_prompt)
           + inspect.getsource(_gate1_dimensions))
    for literal in ("max(90.0", "max(85.0", "max(80.0"):
        assert literal not in src, (
            f"{literal!r}: a hand-copied gate threshold floor is back in the "
            f"prompt builder. Source it from load_gate_dimensions(1) so it "
            f"cannot drift from gate1_per_fr.yaml.")
    assert "load_gate_dimensions(1)" in src, (
        "the GATE1 prompt no longer sources its dimensions from the YAML")


def test_overall_score_weight_asymmetry_is_pinned(tmp_path):
    """PINS the unified 0.25×4 overall_score formula (was 0.33/0.33/0.34, 3 dims).

    History: see module docstring. As of 2026-08-11 the prompt's
    overall_score formula is `(0.25 × 4 dims)`, matching gate1_per_fr.yaml.
    Any future change to either the prompt or the YAML must update this
    test in the same commit — that is the deliberate-change trade the
    pin enforces.
    """
    prompt = _render("GATE1", tmp_path)
    weights = _gate1_dim("weight")
    # Round 45 站4: the prompt renders the formula FROM these weights, so the
    # assertion is now an equivalence rather than a literal pin — a weight
    # change in the YAML flows into the prompt and this still holds, while a
    # prompt that stopped rendering the formula does not.
    for _dim, _w in weights.items():
        assert f"{_dim}.score × {_w:g}" in prompt, (
            f"GATE1 prompt's overall_score formula does not carry {_dim} at "
            f"the weight gate1_per_fr.yaml declares ({_w})")
    assert all(weights[d] == 0.25 for d in weights), (
        "gate1_per_fr.yaml dimension weights diverged from 0.25 — the "
        "documented prompt(0.25×4)/YAML(0.25×4) unification must be "
        "re-adjudicated")
    assert "architecture_constraints" in weights, (
        "gate1_per_fr.yaml no longer declares architecture_constraints — "
        "the Pin must be re-evaluated together with the dimension set")

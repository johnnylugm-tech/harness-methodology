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
    """Legitimises the prompt sourcing thresholds from _GATE1_DIMENSION_STANDARD:
    gate1_per_fr.yaml's tool-dim thresholds MUST equal the standard table.
    If gate1 ever needs distinct thresholds, this fails — forcing a dedicated
    gate1 SSOT rather than a silently-wrong prompt."""
    thr = _gate1_dim("threshold")
    for name in ("linting", "type_safety", "test_coverage"):
        assert thr[name] == _GATE1_DIMENSION_STANDARD[name], (
            f"{name}: gate1_per_fr.yaml threshold {thr[name]} != standard "
            f"{_GATE1_DIMENSION_STANDARD[name]} — prompt sources the standard, "
            f"so a divergence here silently mis-instructs GATE1 sub-agents")


def test_no_unbound_hardcoded_threshold_in_prompt():
    """Completeness /母體封口: the GATE1 threshold floor must be sourced from
    the SSOT, never a re-introduced literal. A future `max(90.0, ...)` slip is
    exactly the finding-A drift class; this makes it fail at author time."""
    from cli.fr_prompts.gate import build_gate1_prompt
    src = inspect.getsource(_build_fr_step_prompt) + inspect.getsource(build_gate1_prompt)
    for literal in ("max(90.0", "max(85.0", "max(80.0"):
        assert literal not in src, (
            f"{literal!r}: a hand-copied gate threshold floor is back in the "
            f"prompt builder. Source it from _GATE1_DIMENSION_STANDARD (finding "
            f"A) so it cannot drift from gate1_per_fr.yaml.")
    assert "_GATE1_DIMENSION_STANDARD" in src, (
        "the GATE1 prompt no longer sources thresholds from the SSOT")


def test_overall_score_weight_asymmetry_is_pinned(tmp_path):
    """DEFERRED finding-A item (see module docstring): the prompt's 0.33/0.34
    overall_score weights diverge from gate1_per_fr.yaml's 0.25×4. gate1 has no
    CRG override so the agent's reported overall_score is adopted — the prompt
    is the de-facto authority; the YAML weight is dead config for gate1. This
    PINS the current asymmetry so any change is deliberate. Re-open: when
    overall_score's dim set is adjudicated."""
    prompt = _render("GATE1", tmp_path)
    assert "× 0.33" in prompt and "× 0.34" in prompt, (
        "GATE1 prompt no longer teaches the 0.33/0.33/0.34 overall_score "
        "weights — if this changed deliberately, re-adjudicate the asymmetry")
    weights = _gate1_dim("weight")
    assert weights["linting"] == 0.25, (
        "gate1_per_fr.yaml linting weight changed from 0.25 — the documented "
        "prompt(0.33)/YAML(0.25) asymmetry must be re-evaluated")

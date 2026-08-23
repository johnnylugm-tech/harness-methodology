"""Gate 1's composite floor is declared once, in the YAML, like every other gate.

Round 70 站1. `core/quality_gate/gate_thresholds.load_score_gate` has been the
single source for "composite ≥ N" since Round 39, and gates 2/3/4 declare the
number in their own YAML (75/80/85). Gate 1 never declared it, so
`load_score_gate(1)` returned None and four places answered the question
independently, with three different answers:

  - `harness/harness_bridge.py`'s `ctx.config.get("score_gate", 80)` fallback
    and its comment said 80,
  - `cli/fr_prompts/gate.py`'s GATE1 prompt told the evaluating LLM 80 twice,
  - `cli/fr_cmds.py`'s FR-99 recovery diagnostic said 80.0 (code-review
    follow-up 2026-08-23; before that, 100.0),
  - and what `finalize_gate` ACTUALLY compared against was **1.0**, because
    `GateConfig.from_dict` fell back to `raw.get("gate", 75)` and
    `gate1_per_fr.yaml`'s `gate:` key is the gate NUMBER, not a score.

Measured on the real projects before fixing: taskq-cc and taskq-api both
resolve `GateConfig.from_dict(gate1_per_fr.yaml, 1).score_gate` to 1.0, and
all four projects checked carry the same YAML and the same profile.py.

The composite verdict itself does not change — 72 scored Gate 1 FRs across 8
projects have a minimum of 97.46, so `>= 1.0` and `>= 80` agreed on every one
of them. What the missing declaration DID cost is visible in the plan
documents: `scripts/plangen/blocks.py` guards its whole composite clause with
`if score_gate is not None`, so every project's `phase3_plan.md` states Gate
2's `composite ≥ 75 [... D4 spec-coverage unified ≥60%]` and states nothing of
the kind for Gate 1 — the D4 threshold rides in the same clause and vanished
with it. taskq / taskq-api / taskq-cc are byte-identical on this.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.quality_gate.constitution.profile import GateConfig
from core.quality_gate.gate_thresholds import load_score_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE1_YAML = REPO_ROOT / "harness" / "gate_configs" / "gate1_per_fr.yaml"


def test_gate1_declares_its_composite_floor_in_the_yaml() -> None:
    """Gate 1 is not the one gate whose floor lives nowhere."""
    assert load_score_gate(1) == 80.0, (
        "load_score_gate(1) is the SSOT every other gate's consumers read; "
        "Gate 1 returning None is what forced four hardcoded copies"
    )


def test_the_config_finalize_gate_actually_compares_against_is_that_number() -> None:
    """`GateConfig.from_dict` is what `harness_bridge._load_config` builds and
    what `finalize_gate` reads `score_gate` off. Before this round it produced
    1.0 for Gate 1 (measured on taskq-cc and taskq-api)."""
    raw = yaml.safe_load(_GATE1_YAML.read_text(encoding="utf-8"))
    cfg = GateConfig.from_dict(raw, 1)
    assert cfg.score_gate == 80.0, (
        f"finalize_gate compares the composite against {cfg.score_gate}, not the "
        "80 that the GATE1 prompt and harness_bridge's own comment both claim"
    )


def test_a_gate_number_is_never_read_as_a_score_threshold() -> None:
    """The root cause, isolated: `gate:` is which gate this is, not what it
    demands. A config that declares no `score_gate` must not inherit its own
    gate number as one."""
    cfg = GateConfig.from_dict({"gate": 7, "dimensions": []}, 7)
    assert cfg.score_gate != 7.0, (
        "raw.get('score_gate', raw.get('gate', 75)) read the gate NUMBER as a "
        "composite threshold — that is how Gate 1's bar became 1.0"
    )


def test_the_gate1_prompt_states_the_number_the_gate_enforces() -> None:
    """`cli/fr_prompts/gate.py` already renders its dimension table from the
    YAML (Round 45 站4). The composite floor was the one number still typed in,
    and it disagreed with what finalize_gate enforced."""
    from cli.fr_prompts import gate as gate_prompts

    src = Path(gate_prompts.__file__).read_text(encoding="utf-8")
    assert "effective_score_gate" in src, (
        "the composite floor in the GATE1 prompt must be read from the same "
        "function finalize_gate resolves its own bar with — a typed copy is "
        "what this round found disagreeing with the enforced value"
    )
    assert ">= 80)" not in src, (
        "the GATE1 prompt still hardcodes the composite floor; interpolate "
        "effective_score_gate(1) instead"
    )


def test_the_gate1_plan_line_states_a_composite_floor_and_a_d4_floor() -> None:
    """What the missing declaration actually cost the projects.

    `scripts/plangen/blocks._build_gate_meta` guards its whole composite clause
    with `if score_gate is not None`, and the D4 spec-coverage floor is inside
    that same clause. Measured on taskq / taskq-api / taskq-cc (byte-identical
    on this): `phase3_plan.md:161` states Gate 2's `composite ≥ 75 [… · D4
    spec-coverage unified ≥60%]` while `:107`, the Gate 1 line, states four
    dimension thresholds and nothing else — so no project's plan has ever said
    what composite Gate 1 demands, or that its per-FR D4 floor is 40%.
    """
    from scripts.plangen.blocks import _build_gate_meta

    _score_gate, _n_dims, prose = _build_gate_meta()[1]
    assert "composite ≥ 80" in prose, prose
    assert "D4 spec-coverage unified ≥40%" in prose, prose


def test_the_gate1_d4_floor_matches_what_the_dispatch_prompt_passes() -> None:
    """The plan's 40% and the GATE1 prompt's `--threshold 40.0` are two
    statements of one number, in two packages that do not import each other.
    Round 70 站1 registered the plan-side entry (it was simply absent); this
    holds the pair together rather than letting the second one drift the way
    the first one's absence did."""
    from scripts.plangen.blocks import _SPEC_COVERAGE_THRESHOLDS

    prompt_src = (
        REPO_ROOT / "scripts" / "workflowgen" / "spec_phase3.py"
    ).read_text(encoding="utf-8")
    floor = _SPEC_COVERAGE_THRESHOLDS[1]
    assert f"--threshold {floor:g}.0 --fr-id" in prompt_src, (
        f"the plan says Gate 1's D4 floor is {floor:g}% but the GATE1 dispatch "
        "prompt passes a different --threshold to spec-coverage-check"
    )


def test_the_fr99_recovery_diagnostic_reads_the_same_source() -> None:
    """`cli/fr_cmds.py:_detect_evaluator_passed_but_commit_uncommitted` compares
    both the ephemeral and the durable score against this floor. It carried its
    own literal (100.0, then 80.0) — the fourth statement of one number."""
    src = (REPO_ROOT / "cli" / "fr_cmds.py").read_text(encoding="utf-8")
    assert "score_gate: float = 100.0" not in src
    assert "score_gate: float = 80.0" not in src, (
        "the FR-99 recovery diagnostic still types the floor; it must read "
        "effective_score_gate(1) like every other consumer"
    )
    assert "effective_score_gate" in src


def test_declared_and_enforced_cannot_disagree() -> None:
    """The one function every consumer reads. A gate that declares a floor
    gets that floor; one that declares none gets the same default
    `GateConfig.from_dict` applies — never a literal typed at the call site,
    which is how three consumers came to state 80 while the verdict used 1.0."""
    from core.quality_gate.constitution.profile import (
        UNDECLARED_SCORE_GATE,
        effective_score_gate,
    )

    for gate in (1, 2, 3, 4):
        assert effective_score_gate(gate) == load_score_gate(gate)

    undeclared = GateConfig.from_dict({"gate": 9, "dimensions": []}, 9)
    assert undeclared.score_gate == UNDECLARED_SCORE_GATE

"""Gate dimension thresholds, read from the gate_configs YAML that enforces them.

`harness/gate_configs/gate{1,2,3,4}_*.yaml` is what HarnessBridge._load_config
actually scores against, so it is the only authority on a dimension's
threshold. Everything else that states a threshold — the GATE1 dispatch
prompt, plan prose, workflow prose, the NFR-backed override floor — must read
it from here rather than keep its own copy.

This module stores NO threshold values. It is a reader, not a second source:
adding a constant table here would recreate exactly the drift this exists to
remove (Round 18 站2; the same reasoning that kept Round 17 站1 from minting a
new gate_rules.py).

Drift this closes, measured: 35214a0 raised Gate 1's linting/type_safety from
90/85 to 100/100 in gate1_per_fr.yaml and in two hand-maintained copies, and
left six others saying 90/85 — four P*_SOP.md files, the phase flowchart, and
the committed phase3 plan.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = [
    "load_gate_thresholds",
    "load_gate_dimensions",
    "load_score_gate",
    "framework_owned_dimensions",
    "gate_config_path",
    "gate_scope",
    "is_per_fr_gate",
    "GATE_CONFIG_NAMES",
]

# Mirrors HarnessBridge._load_config's own mapping (harness/harness_bridge.py).
GATE_CONFIG_NAMES: dict[int, str] = {
    1: "gate1_per_fr.yaml",
    2: "gate2_p3_exit.yaml",
    3: "gate3_p4_exit.yaml",
    4: "gate4_p6_full.yaml",
}

# The framework's own gate_configs — the same tree harness_bridge.py resolves
# via `Path(__file__).parent / "gate_configs"`. Thresholds are framework
# policy, not per-project config, so this is deliberately NOT project-relative.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def gate_config_path(gate_num: int) -> Path:
    """Return the YAML path for *gate_num*, raising ValueError on a bad gate."""
    if gate_num not in GATE_CONFIG_NAMES:
        raise ValueError(
            f"gate_num must be one of {sorted(GATE_CONFIG_NAMES)}; got {gate_num}"
        )
    return _REPO_ROOT / "harness" / "gate_configs" / GATE_CONFIG_NAMES[gate_num]


@lru_cache(maxsize=None)
def _read_gate_config(gate_num: int) -> dict:
    import yaml  # type: ignore[import-untyped]

    raw = yaml.safe_load(gate_config_path(gate_num).read_text(encoding="utf-8"))
    return raw or {}


def _read_gate_dimensions(gate_num: int) -> list[dict]:
    return [
        d
        for d in _read_gate_config(gate_num).get("dimensions", [])
        if isinstance(d, dict) and "name" in d and "threshold" in d
    ]


def load_gate_dimensions(gate_num: int) -> list[dict]:
    """Return *gate_num*'s dimension entries, in the order the YAML declares them.

    The order is load-bearing for anything that renders the list into prose:
    a set would make the generated text reorder itself between runs and turn
    every regeneration into a diff.

    Entries are copied for the same reason ``load_gate_thresholds`` copies —
    the read is cached, so handing out the cached dicts would let one caller's
    edit reach every later reader.
    """
    return [dict(d) for d in _read_gate_dimensions(gate_num)]


def all_gate_dimension_names() -> frozenset[str]:
    """Every dimension name any gate config declares — the whole pipeline's set.

    Round 83 站5. "Is this dimension in THIS gate" and "is this dimension
    measured ANYWHERE" are different questions, and the second is the one a
    project's quality manifest is asking when it pins an NFR to a dimension.
    Measured across the eleven projects on this machine: the union below is 18
    names, the python registry has 16, and `registry - union` is EMPTY — so
    every dimension a manifest can legally name is measured by some gate, and
    a per-gate answer to the pipeline's question can only ever report the
    framework's own gate layering back at itself.

    Reads through `load_gate_dimensions`, which is `lru_cache`d, rather than
    opening the YAMLs again: a second reader of the same files is a second
    answer waiting to disagree with the first.
    """
    return frozenset(
        str(d["name"])
        for gate_num in GATE_CONFIG_NAMES
        for d in load_gate_dimensions(gate_num)
        if d.get("name")
    )


def load_score_gate(gate_num: int) -> float | None:
    """Return the composite score a gate must reach, or None if it declares none.

    ``score_gate`` is the number the prose calls "composite ≥ N". It lives in
    the same YAML as the per-dimension thresholds and is read the same way, for
    the same reason.
    """
    value = _read_gate_config(gate_num).get("score_gate")
    return None if value is None else float(value)


def gate_scope(gate_num: int) -> str:
    """Return what this gate judges over: ``single_fr`` / ``full_phase`` / ``full_project``.

    Round 57 站1. The YAML has carried `scope:` since Gate 1 existed and
    nothing read it, so "is this coverage number about one FR or about the
    whole tree" was answered three separate times by three phase conditions
    that disagreed — S4 had none (any phase re-scoped per FR),
    `validate_fr_coverage_immediate` and `_check_gate1_live_coverage` each
    tested for phase 3. On a Phase 7 tree the two enforcers reached opposite
    verdicts about the same run.

    The declaration is not a fourth copy: it is the one the gate makes about
    itself, at the point the gate is defined, and Gate 1 is `single_fr` at
    every phase it runs (P3/P4/P5/P7/P8/P9). Reading it removes three
    conditions rather than adding one.

    An unreadable or absent `scope:` raises rather than defaulting. A gate
    whose config does not say what it judges cannot be judged — same contract
    as `gate_config_path` on an unknown gate number (Round 30 站3).
    """
    value = _read_gate_config(gate_num).get("scope")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"gate {gate_num} config declares no `scope:` "
            f"({gate_config_path(gate_num)}). Expected framework-owned asset — "
            f"is the harness checkout intact?"
        )
    return value.strip()


def is_per_fr_gate(gate_num: int) -> bool:
    """True when *gate_num* judges one FR at a time.

    The single predicate behind every "should this be scoped to the FR"
    branch, for the same reason `na_is_framework_verified` is single: two
    copies of the question is two chances to drift apart.
    """
    return gate_scope(gate_num) == "single_fr"


def framework_owned_dimensions(gate_num: int) -> dict[str, str]:
    """Return ``{dimension: tool}`` for the dimensions the harness scores itself.

    Two shapes, both derived from the YAML rather than from a list kept here:

    * ``requires_tool_execution: false`` — traceability and adversarial_review;
      finalize_gate patches their scores in (harness_bridge's S4 skips them).
    * ``tool: code-review-graph`` — architecture. It does require tool
      execution, but the tool is the framework's own: ``crg_independent``
      computes the score in finalize_gate and overrides whatever the agent
      wrote. harness_bridge's ``_TOOL_OUTPUT_PATTERNS`` states the same
      exception for the same reason.

    An agent that self-scores one of these is writing a number the framework is
    about to replace, so every prompt that enumerates dimensions has to say
    which ones they are — and saying it from here keeps that sentence correct
    when a gate config gains or loses one (Round 38 站1 added architecture to
    gate 2; three hand-written prompts did not notice).
    """
    return {
        str(d["name"]): str(d.get("tool", ""))
        for d in _read_gate_dimensions(gate_num)
        if d.get("requires_tool_execution") is False
        or d.get("tool") == "code-review-graph"
    }


def _read_gate_thresholds(gate_num: int) -> dict[str, float]:
    return {
        str(d["name"]): float(d["threshold"])
        for d in _read_gate_dimensions(gate_num)
    }


def load_gate_thresholds(gate_num: int) -> dict[str, float]:
    """Return ``{dimension_name: threshold}`` for *gate_num*, from its YAML.

    The YAML read is cached (framework policy, constant within a run, and
    callers include prompt builders that run per FR step); the dict returned
    is a fresh copy each call so a caller mutating its own view cannot poison
    every later reader — the shallow-copy footgun this codebase has already
    been bitten by once (ScoringProfile.dimension_keywords).
    """
    return dict(_read_gate_thresholds(gate_num))

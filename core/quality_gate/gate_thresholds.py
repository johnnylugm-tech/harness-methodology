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

__all__ = ["load_gate_thresholds", "gate_config_path", "GATE_CONFIG_NAMES"]

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
def _read_gate_thresholds(gate_num: int) -> dict[str, float]:
    import yaml  # type: ignore[import-untyped]

    raw = yaml.safe_load(gate_config_path(gate_num).read_text(encoding="utf-8"))
    return {
        str(d["name"]): float(d["threshold"])
        for d in (raw or {}).get("dimensions", [])
        if isinstance(d, dict) and "name" in d and "threshold" in d
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

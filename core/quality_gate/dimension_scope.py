"""Which dimensions a gate actually measured, and why some were skipped.

Round 39 站2. Three dimensions can be switched off in
`.methodology/harness_config.json` — `mutation_testing`, `architecture`
(`crg_architecture`) and `adversarial_review` (`phase4_llm_review`). Switching
one off is legitimate: a project may genuinely have no mutmut, or no
code-review-graph. What was not legitimate is that it happened invisibly.

Measured before this module existed, a `false` would:

  * drop the dimension from `harness_bridge`'s config list *and* from the
    scored dimensions — not measured, not compared, not blocking;
  * make `cmd_crg_arch_check` return 0 on the spot, so CI's absolute
    architecture floor became an unconditional pass;
  * skip Gate 4's B3 check that CRG reconnaissance output exists at all;

and leave behind exactly one `print()`. Nothing in the degradation ledger,
the quality manifest, the gate result, or Round 38 站4's `gate_verify.jsonl`.
A committed boolean could change the verdict while the verdict showed no sign
of it.

Round 30's rule is that abstaining is not passing. Round 38 站4 made a verdict
carry the tree it was measured on; this makes it carry the dimensions it was
measured over. Same discipline, one level up from Round 37's file sets: the
denominator travels with the number.

The switch stays. Its invisibility does not.
"""

from __future__ import annotations

from pathlib import Path

from core.degradation_ledger import record_degradation
from core.harness_config import _DIM_TO_FEATURE, load_harness_config

__all__ = ["COMPONENT", "disabled_dimensions", "record_dimension_scope"]

# Ledger component, matching the "area:what" shape the other recorders use
# (`crg:graph-scope`, `crg:baseline`, `mutation:scope`).
COMPONENT = "gate:dimension-disabled"


def disabled_dimensions(project: "str | Path") -> dict[str, str]:
    """``{dimension: feature_key}`` for every dimension switched off.

    Reads `core.harness_config._DIM_TO_FEATURE` rather than restating the
    mapping — `scripts/plangen/blocks.py` carried a hand-written mirror of it,
    which is how a dimension can end up disabled in the plan and enabled in
    the gate.
    """
    features = load_harness_config(project)
    return {
        dim: feat for dim, feat in _DIM_TO_FEATURE.items()
        if not features[feat]
    }


def record_dimension_scope(
    project: "str | Path", gate: "int | None" = None,
) -> list[str]:
    """Record every disabled dimension in the ledger; return their names.

    Returns a sorted list so callers can embed it in an artifact verbatim. A
    project with nothing disabled writes nothing — the ledger is for events,
    and "the framework measured what it says it measures" is not one.
    """
    disabled = disabled_dimensions(project)
    where = f"gate {gate}" if gate is not None else "this run"
    for dim in sorted(disabled):
        record_degradation(
            project, COMPONENT,
            f"{dim} was not evaluated at {where}",
            why=(f"features.{disabled[dim]} is false in "
                 f".methodology/harness_config.json — the dimension is not "
                 f"measured, not compared against its threshold, and not "
                 f"blocking"),
        )
    return sorted(disabled)

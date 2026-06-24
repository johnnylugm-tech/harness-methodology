"""Global per-project harness feature flags.

Config file: <project>/.methodology/harness_config.json
Schema version 1::

    {
      "version": 1,
      "features": {
        "mutation_testing": false,
        "phase4_llm_review": true,
        "crg_architecture": true
      }
    }

Missing file or malformed JSON → hardcoded defaults (no crash).
Unknown keys are silently ignored (forward-compatible).
"""
import json
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "mutation_testing": False,
    "phase4_llm_review": True,
    "crg_architecture": True,
}

# Dimension name → harness_config.json feature key.
# Single source of truth for the mapping; imported by harness_bridge,
# harness_cli, and generate_full_plan.  Keep in sync with _DEFAULTS.
_DIM_TO_FEATURE: dict[str, str] = {
    "mutation_testing": "mutation_testing",
    "architecture": "crg_architecture",
    "adversarial_review": "phase4_llm_review",
}


def load_harness_config(project: "str | Path") -> dict:
    """Return the merged features dict (file values overlaid on defaults)."""
    cfg_path = Path(project) / ".methodology" / "harness_config.json"
    if not cfg_path.exists():
        return dict(_DEFAULTS)
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        features = raw.get("features", {})
        return {k: features.get(k, v) for k, v in _DEFAULTS.items()}
    except Exception:
        return dict(_DEFAULTS)


def get_feature(project: "str | Path", key: str) -> Any:
    """Return the value of a single feature flag.

    Returns None for unknown keys (keys not in _DEFAULTS).
    """
    return load_harness_config(project).get(key, _DEFAULTS.get(key))


def is_dim_disabled(dim_name: str, project_root: "str | Path") -> bool:
    """True when this dimension's feature flag is disabled.

    Looks up the dimension name in ``_DIM_TO_FEATURE`` to find its
    feature-flag key, then checks ``get_feature()``.  Dimensions with
    no mapping are never disabled (returns False).
    """
    feat = _DIM_TO_FEATURE.get(dim_name)
    if feat is None:
        return False
    return not get_feature(project_root, feat)

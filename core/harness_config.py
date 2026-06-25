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

    The returned dict from ``load_harness_config`` already contains every
    key in ``_DEFAULTS`` (file value overlaid on default), so this returns
    ``None`` only when *key* is not in ``_DEFAULTS`` and the loaded dict
    has no entry for it.
    """
    return load_harness_config(project).get(key)


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


# ══════════════════════════════════════════════════════════════════════════════
# Stall / timeout thresholds — single source of truth
# ══════════════════════════════════════════════════════════════════════════════
#
# Before this dict existed, timeouts were hardcoded at 6+ call sites in
# harness_cli.py and other modules, with values ranging from 300s to 3600s.
# Adding a new timeout meant grepping every call site; tuning one required
# editing many files; debugging "why is gate X stalling?" required reading
# multiple modules. Centralise them here so a future operator can change
# them in one place and add new keys for new code paths.
#
# Keys:
#   subprocess      subprocess.run timeout (CLI gate runs)
#   task_default    per-task timeout during normal phases
#   task_dev        per-task timeout during P1/P2 development (more lenient)
#   fr_step         default --timeout for `run-fr-step` GATE1-DELTA
#   mutation        cap on mutation testing run (an entire gate)
#   state_alert_min base minutes before a phase alerts on no-progress
#
# All values are in seconds (or minutes for *_min keys).

STALL_TIMEOUTS: dict[str, int] = {
    "subprocess": 300,
    "task_default": 300,
    "task_dev": 1200,
    "fr_step": 600,
    "mutation": 3600,
    "state_alert_min": 180,
}


def get_timeout(key: str) -> int:
    """Return the stall/timeout threshold for ``key``.

    Raises ``KeyError`` for unknown keys. A typo'd key (e.g. ``"subproc"``
    instead of ``"subprocess"``) used to silently fall back to 600s,
    which would 2x the wallclock of env-check / wiki-update subprocesses
    or 6x-shorten mutation testing runs with no log line. Strict mode
    surfaces the typo at the first call site.
    """
    try:
        return int(STALL_TIMEOUTS[key])
    except KeyError as exc:
        valid = ", ".join(sorted(STALL_TIMEOUTS))
        raise KeyError(
            f"unknown STALL_TIMEOUTS key: {key!r}; valid keys: {valid}"
        ) from exc

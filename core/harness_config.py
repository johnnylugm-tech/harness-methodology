"""Global per-project harness configuration — the ONE place a tunable lives.

Config file: <project>/.methodology/harness_config.json
Schema version 1 (the values below are illustrative overrides, not the
defaults — those are ``_DEFAULTS`` / ``_VALUE_DEFAULTS`` in this module, and
docs/CONFIGURATION.md tabulates them)::

    {
      "version": 1,
      "features": {
        "mutation_testing": false,
        "phase4_llm_review": true,
        "crg_architecture": true,
        "cross_artifact_live_cov": false,
        "security_design": true
      },
      "values": {
        "drift_threshold": 85.0,
        "max_fix_rounds": 3,
        "permission_mode": "bypassPermissions",
        "timeouts": {"mutation": 7200},
        "step_max_turns": {"GATE1": 90}
      },
      "crg_cohesion_healthy": 0.2,
      "crg_excludes": [".claude/*", "*.mjs"]
    }

``features`` holds boolean flags (see ``load_harness_config``); ``values``
holds tunable parameters (see ``get_value``). Every default equals the
behavior the framework shipped with before the key existed — an absent or
empty config changes nothing. The full key reference lives in
docs/CONFIGURATION.md (a meta-test keeps registry and doc in sync).

The top-level ``crg_*`` value keys calibrate the framework-owned CRG
architecture score (see ``get_crg_settings``):

* ``crg_cohesion_healthy`` — per-project cohesion floor for a community
  to count as healthy (float in (0, 1]). Small packages (≤ ~10 source
  files) may calibrate below the 0.3 default because Leiden community
  detection over-fragments at that scale.
* ``crg_excludes`` — fnmatch globs over repo-relative file paths; a
  community whose files are majority-matched is excluded from scoring
  (project tooling such as workflow scripts is not product code).

Missing file or malformed JSON → hardcoded defaults (no crash). Unknown
keys warn once per process and are otherwise ignored (Round 9: the audit
found zombie settings — enforcement.json's dataclass, SSI config.yaml —
whose silent no-op edits were worse than a crash; a typo'd key here used
to be the same trap). A value of the wrong type/range also warns and
falls back to the default — the gate pipeline must never crash on a
hand-edited config.
"""
import json
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    # Round 9: promoted from the HARNESS_CROSS_ARTIFACT_COV env var (which
    # still wins when set, for per-invocation override) — run live pytest
    # --cov during finalize-gate cross-artifact checks instead of reusing
    # .coverage data (costs up to ~120s per gate call).
    "cross_artifact_live_cov": False,
    # Round 10: gap-analysis response. Defaults to True — a DELIBERATE
    # behavior change (unlike every other key here, whose default preserves
    # pre-existing behavior). Gates core.quality_gate.security_design's
    # structural SAD.md §6 threat-model check. An honest `applicability:
    # none` + justification always passes (see security_design.py) — this
    # is not a keyword-density scorer (that mechanism was proven to
    # false-positive-fail honest tool-type projects; see Bug #35 and
    # ConstitutionProfile's P1/P3/P4 security-dimension removal).
    "security_design": True,
}

# Round 60 站2 — the three flags that could take a dimension out of the
# judgement, and the tombstone that replaces them.
#
# `mutation_testing`, `crg_architecture` (the `architecture` dimension) and
# `phase4_llm_review` (`adversarial_review`) each removed a dimension from the
# gate's dimension list. Nothing else rode on them: a repository-wide scan
# found no use outside that one dimension's own enforcement chain.
#
# Removing a dimension RAISES the composite (the mean is taken over what was
# scored), and the file that removes it — `.methodology/harness_config.json` —
# is committed by the project being judged. Round 39 站2 made the switch
# visible; visibility was not the problem. Measured across the eight corpus
# projects on 2026-08-19: three carried `mutation_testing: false`, and the
# prompt rule written to explain that state to the Gate 2 orchestrator
# (`f4be095`) had to invent the project's motive because the framework has no
# field recording one.
#
# A dimension is measured, or the gate blocks and the run routes to repair. A
# tool that cannot run is an INFRA fact (Round 32 站4), never a quiet
# subtraction from the denominator.
#
# The names are tombstoned rather than forgotten: silently ignoring
# `mutation_testing: false` would leave a project believing it had switched
# something off. Same discipline as EX_RETIRED_CONSTITUTION_GATE — the name
# is not reused, and its presence is reported.
RETIRED_FEATURES: frozenset[str] = frozenset({
    "mutation_testing", "crg_architecture", "phase4_llm_review",
})


def retired_disabling_keys(features: "dict | None") -> list[str]:
    """Sorted retired keys in *features* that ask for a dimension to be off.

    A retired key set to ``true`` asks for nothing that is not already the
    rule, so it is left to the existing unknown-key WARN; blocking on it would
    refuse a config that agrees with us. Pure, so the block and its test share
    no seam.
    """
    if not isinstance(features, dict):
        return []
    return sorted(k for k in RETIRED_FEATURES if k in features and not features[k])


# Top-level keys the schema knows. Anything else in the file is a typo or
# a leftover from an older schema — warn, don't silently no-op (P3 of the
# Round 9 audit: "unknown keys silently ignored" is how "mutation_testng"
# quietly runs with the default).
_KNOWN_TOP_LEVEL = {"version", "features", "values",
                    "crg_cohesion_healthy", "crg_excludes"}

# Warn once per (section, key) per process — load_harness_config is called
# a dozen times in a single CLI run and a repeated wall of WARNs helps
# nobody; one line per typo does.
_warned_unknown: set = set()


def _warn_unknown(section: str, present, known) -> None:
    for key in sorted(set(present) - set(known)):
        if (section, key) in _warned_unknown:
            continue
        _warned_unknown.add((section, key))
        print(f"[harness-config] WARN: unknown {section} key {key!r} ignored "
              f"(valid: {', '.join(sorted(known))})")


def _read_raw(project: "str | Path") -> dict:
    """The raw config dict; missing file / bad JSON / non-dict → {}."""
    cfg_path = Path(project) / ".methodology" / "harness_config.json"
    if not cfg_path.exists():
        return {}
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        if ("malformed", str(cfg_path)) not in _warned_unknown:
            _warned_unknown.add(("malformed", str(cfg_path)))
            print(f"[harness-config] WARN: {cfg_path} unreadable ({exc}) — "
                  f"using built-in defaults")
        return {}
    return raw if isinstance(raw, dict) else {}


def load_harness_config(project: "str | Path") -> dict:
    """Return the merged features dict (file values overlaid on defaults)."""
    raw = _read_raw(project)
    if not raw:
        return dict(_DEFAULTS)
    _warn_unknown("top-level", raw, _KNOWN_TOP_LEVEL)
    features = raw.get("features", {})
    if not isinstance(features, dict):
        return dict(_DEFAULTS)
    _warn_unknown("features", features, _DEFAULTS)
    return {k: features.get(k, v) for k, v in _DEFAULTS.items()}


def get_feature(project: "str | Path", key: str) -> Any:
    """Return the value of a single feature flag.

    The returned dict from ``load_harness_config`` already contains every
    key in ``_DEFAULTS`` (file value overlaid on default), so this returns
    ``None`` only when *key* is not in ``_DEFAULTS`` and the loaded dict
    has no entry for it.
    """
    return load_harness_config(project).get(key)


# ══════════════════════════════════════════════════════════════════════════════
# Tunable values — the ``values`` section
# ══════════════════════════════════════════════════════════════════════════════
#
# Registry of every tunable parameter. Every default IS the pre-Round-9
# hardcoded behavior; consumers pass through get_value so the precedence
# chain stays: CLI flag > per-FR fr_config (quality_manifest) > this file >
# these defaults. Anti-backdoor rule (docs/CONFIGURATION.md): values that
# guard gate integrity (gate1 coverage floor, milestone entry gates, ghost
# detection) are deliberately NOT here and must not be added.

_VALUE_DEFAULTS: dict[str, Any] = {
    # M2 drift-detection ensemble score threshold (0-100). Consumed by the
    # PhaseHooks construction sites in cli/phase_cmds, cli/gate_cmds, and
    # core/adapters/phase_hooks_adapter.
    "drift_threshold": 85.0,
    # Global default for run-fr-step fix rounds (per-FR fr_config and the
    # --max-fix-rounds CLI flag both override).
    "max_fix_rounds": 3,
    # permission_mode passed to spawned sub-agents (--permission-mode wins).
    "permission_mode": "bypassPermissions",
    # Per-key overlay onto STALL_TIMEOUTS, e.g. {"mutation": 7200}.
    "timeouts": {},
    # Per-step overlay onto cli/fr_cmds._STEP_MAX_TURNS, e.g. {"GATE1": 90}.
    "step_max_turns": {},
    # HR-11 Phase Truth verification score floor (0-100). Round 9 station 3:
    # migrated from enforcement.json's hr_overrides.HR-11_phase_truth_threshold
    # (still honored as a fallback by phase_truth_verifier, with a migration
    # nudge).
    "phase_truth_threshold": 90.0,
    # Phase Truth pytest run cap in seconds (SG-5; floor 30 enforced by the
    # consumer). Migrated from enforcement.json's phase_truth.pytest_timeout_seconds.
    "phase_truth_pytest_timeout": 300,
    # Round 12 站3c — per-checker enforcement level overlay,
    # e.g. {"spec_unsatisfiable": "block"}. Values: "block" | "warn".
    # Policy (docs/CONFIGURATION.md): EXISTING checkers keep their
    # hard-coded block behavior (zero behavior change — this dict cannot
    # weaken them; only checkers that explicitly consult
    # get_checker_enforcement participate). NEW checkers and NEW
    # tightenings of existing ones ship consulting this overlay with
    # default "warn", and are promoted to "block" only after one E2E run
    # with zero false kills — the R5 incident (a tightening that was
    # mathematically unsatisfiable for correct code, hard-BLOCKing the
    # pipeline for hours) is the reason graduation exists.
    "checker_enforcement": {},
    # Round 45 站1 — per-file ceiling for copying a dimension's cited
    # tool_output into .methodology/gate_evidence/ so the verdict outlives the
    # gitignored work directory. The largest cited evidence measured across
    # five projects was 19,994 bytes; this is a pathology guard an order of
    # magnitude above that, not a routine path. Over the ceiling the citation
    # stays pointed at the original and the ledger records why.
    "gate_evidence_max_bytes": 1_048_576,
}


def _valid_value(key: str, v: Any) -> bool:
    """Type/range validation for a ``values`` entry (registry-declared)."""
    if key in ("drift_threshold", "phase_truth_threshold"):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < float(v) <= 100
    if key in ("max_fix_rounds", "phase_truth_pytest_timeout",
               "gate_evidence_max_bytes"):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 1
    if key == "permission_mode":
        return isinstance(v, str) and bool(v)
    if key in ("timeouts", "step_max_turns"):
        return isinstance(v, dict) and all(
            isinstance(k, str)
            and isinstance(n, int) and not isinstance(n, bool) and n >= 1
            for k, n in v.items()
        )
    if key == "checker_enforcement":
        return isinstance(v, dict) and all(
            isinstance(k, str) and lv in ("block", "warn")
            for k, lv in v.items()
        )
    return False


def get_value(project: "str | Path", key: str) -> Any:
    """Return one tunable from the ``values`` section, or its default.

    Unknown *key* raises KeyError (a consumer typo is a programming error —
    same strict contract as ``get_timeout``). A file value that fails the
    registry's type/range check warns and falls back to the default.
    """
    if key not in _VALUE_DEFAULTS:
        valid = ", ".join(sorted(_VALUE_DEFAULTS))
        raise KeyError(f"unknown values key: {key!r}; valid keys: {valid}")
    default = _VALUE_DEFAULTS[key]
    values = _read_raw(project).get("values", {})
    if not isinstance(values, dict) or key not in values:
        if isinstance(values, dict):
            _warn_unknown("values", values, _VALUE_DEFAULTS)
        return _copy_default(default)
    _warn_unknown("values", values, _VALUE_DEFAULTS)
    v = values[key]
    if not _valid_value(key, v):
        if ("values-invalid", key) not in _warned_unknown:
            _warned_unknown.add(("values-invalid", key))
            print(f"[harness-config] WARN: values.{key} = {v!r} fails "
                  f"type/range validation — using default {default!r}")
        return _copy_default(default)
    return v


def _copy_default(default: Any) -> Any:
    """Never hand out the registry's own mutable dict."""
    return dict(default) if isinstance(default, dict) else default


def get_checker_enforcement(project: "str | Path", checker: str,
                            default: str = "warn") -> str:
    """Round 12 站3c — enforcement level for one quality-gate checker.

    Reads the ``values.checker_enforcement`` overlay; falls back to
    ``default``. GRADUATION POLICY (the R5 lesson mechanized): checkers
    that consult this function ship with default="warn" and are promoted
    to "block" only after one E2E run with zero false kills — a checker
    false positive deadlocks the pipeline and forces an emergency harness
    fix, which is asymmetrically worse than one run of advisory noise.
    Existing hard-coded-block checkers do NOT consult this function and
    are therefore unaffected (no weakening path by config).
    """
    overlay = get_value(project, "checker_enforcement")
    level = overlay.get(checker, default) if isinstance(overlay, dict) else default
    return level if level in ("block", "warn") else default


def value_is_configured(project: "str | Path", key: str) -> bool:
    """True when the config file itself sets ``values.<key>`` (validly).

    Lets a consumer with a legacy fallback source (phase_truth_verifier's
    enforcement.json keys) distinguish "operator chose this value" from
    "get_value returned the registry default".
    """
    if key not in _VALUE_DEFAULTS:
        valid = ", ".join(sorted(_VALUE_DEFAULTS))
        raise KeyError(f"unknown values key: {key!r}; valid keys: {valid}")
    values = _read_raw(project).get("values", {})
    return isinstance(values, dict) and key in values and _valid_value(key, values[key])


_CRG_VALUE_DEFAULTS: dict[str, Any] = {
    "cohesion_healthy": None,  # None → crg_analysis falls back to env/0.3
    "excludes": [],
}


def get_crg_settings(project: "str | Path") -> dict:
    """Return per-project CRG calibration values.

    Reads the top-level ``crg_cohesion_healthy`` / ``crg_excludes`` keys of
    ``.methodology/harness_config.json`` (NOT nested under ``features`` —
    those are booleans only). Returns::

        {"cohesion_healthy": float | None, "excludes": list[str]}

    Missing file, malformed JSON, or bad types degrade to the defaults —
    the gate must never crash on a hand-edited config.
    """
    settings = {k: (list(v) if isinstance(v, list) else v)
                for k, v in _CRG_VALUE_DEFAULTS.items()}
    cfg_path = Path(project) / ".methodology" / "harness_config.json"
    if not cfg_path.exists():
        return settings
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return settings
    if not isinstance(raw, dict):
        return settings
    thr = raw.get("crg_cohesion_healthy")
    if isinstance(thr, (int, float)) and not isinstance(thr, bool):
        thr_f = float(thr)
        if 0.0 < thr_f <= 1.0:
            settings["cohesion_healthy"] = thr_f
    excludes = raw.get("crg_excludes")
    if isinstance(excludes, list):
        settings["excludes"] = [e for e in excludes if isinstance(e, str)]
    return settings


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
    # env-check spawns a full LLM sub-agent (max-turns 70) that reads the
    # project's SAD/SRS, probes runtime + CLI tools, and writes
    # env_check_result.json. Empirically the sub-agent takes ~1:48 on a
    # warm cache run but ~5:30 on a cold start (workflow entry from a
    # fresh P4 advance). 300s `subprocess` races that cold path; 900s
    # buffers it without conflating with full `task_dev` (1200s).
    "env_check": 900,
    "task_default": 300,
    "task_dev": 1200,
    "fr_step": 600,
    "mutation": 3600,
    "state_alert_min": 180,
    "gitleaks": 300,
}


def get_timeout(key: str, project: "str | Path | None" = None) -> int:
    """Return the stall/timeout threshold for ``key``.

    Raises ``KeyError`` for unknown keys. A typo'd key (e.g. ``"subproc"``
    instead of ``"subprocess"``) used to silently fall back to 600s,
    which would 2x the wallclock of env-check / wiki-update subprocesses
    or 6x-shorten mutation testing runs with no log line. Strict mode
    surfaces the typo at the first call site.

    With *project* (Round 9), the config file's ``values.timeouts`` overlay
    wins for keys it names; ``None`` keeps the built-in table verbatim, so
    every pre-existing call site is untouched. An overlay key that isn't a
    real STALL_TIMEOUTS key warns and is ignored (never silently invents a
    new timeout class).
    """
    if key not in STALL_TIMEOUTS:
        valid = ", ".join(sorted(STALL_TIMEOUTS))
        raise KeyError(f"unknown STALL_TIMEOUTS key: {key!r}; valid keys: {valid}")
    if project is not None:
        overlay = get_value(project, "timeouts")
        _warn_unknown("values.timeouts", overlay, STALL_TIMEOUTS)
        if key in overlay:
            return int(overlay[key])
    return int(STALL_TIMEOUTS[key])

#!/usr/bin/env python3
"""
Constitution Profile — single-source configuration for constitution checking.

Load order (last wins):
  1. Built-in defaults (current hardcoded values — backward compatible)
  2. .methodology/constitution_profile.json (project-level overrides)
  3. METHODOLOGY_CONSTITUTION_PROFILE env var (JSON string)

Usage:
    from core.quality_gate.constitution.profile import load_profile

    profile = load_profile()                     # auto-detect
    profile = load_profile(".methodology/constitution_profile.json")
    profile = load_profile(env="METHODOLOGY_CONSTITUTION_PROFILE")

    # Access
    profile.dimensions["correctness"].keywords
    profile.phases[3].active_dimensions
    profile.file_filters["srs"]
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DimensionProfile:
    """Per-dimension configuration: keywords, threshold, and rule reference."""

    threshold: Optional[float] = None
    keywords: List[str] = field(default_factory=list)
    rule: str = "TH-02"
    hardcoded_secret_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "threshold": self.threshold,
            "keywords": self.keywords,
            "rule": self.rule,
            "hardcoded_secret_patterns": self.hardcoded_secret_patterns,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DimensionProfile":
        return cls(
            threshold=d.get("threshold"),
            keywords=d.get("keywords", []),
            rule=d.get("rule", "TH-02"),
            hardcoded_secret_patterns=d.get("hardcoded_secret_patterns", []),
        )


@dataclass
class PhaseProfile:
    """Per-phase configuration: which dimensions are active and the composite threshold."""

    active_dimensions: List[str] = field(default_factory=list)
    composite_threshold: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "active_dimensions": self.active_dimensions,
            "composite_threshold": self.composite_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PhaseProfile":
        return cls(
            active_dimensions=d.get("active_dimensions", []),
            composite_threshold=d.get("composite_threshold"),
        )


@dataclass
class ScoringProfile:
    """Scoring method configuration."""

    method: str = "min_of_dimensions"

    def to_dict(self) -> dict:
        return {"method": self.method}

    @classmethod
    def from_dict(cls, d: dict) -> "ScoringProfile":
        return cls(method=d.get("method", "min_of_dimensions"))


@dataclass
class ConstitutionProfile:
    """Complete constitution profile — all configurable constitution parameters.

    When no overrides are provided, defaults match the current hardcoded values
    in runner.py and constitution/__init__.py.
    """

    scoring: ScoringProfile = field(default_factory=ScoringProfile)
    phases: Dict[int, PhaseProfile] = field(default_factory=dict)
    dimensions: Dict[str, DimensionProfile] = field(default_factory=dict)
    file_filters: Dict[str, List[str]] = field(default_factory=dict)
    phase_dir_map: Dict[int, str] = field(default_factory=dict)

    # ── helpers ───────────────────────────────────────────────────────────

    def active_dimensions(self, phase: int) -> List[str]:
        """Return the active constitution dimensions for a given phase."""
        p = self.phases.get(phase)
        return p.active_dimensions if p else []

    def composite_threshold(self, phase: int) -> float:
        """Return the composite constitution score threshold for a phase."""
        p = self.phases.get(phase)
        if p and p.composite_threshold is not None:
            return p.composite_threshold
        return 80.0

    def dimension_threshold(self, dim: str, _phase: int = 1) -> float:
        """Return the per-dimension threshold.

        Dimensions use a fixed threshold regardless of phase (P1-P4 only).
        P5+ uses the composite threshold instead. The _phase parameter is
        accepted for caller convenience but does not affect the result.
        """
        d = self.dimensions.get(dim)
        if d and d.threshold is not None:
            return d.threshold
        return 80.0

    def dimension_keywords(self, dim: str) -> List[str]:
        """Return the keyword set for a dimension."""
        d = self.dimensions.get(dim)
        return d.keywords if d else []

    def secret_patterns(self) -> List[str]:
        """Return hardcoded-secret detection patterns."""
        d = self.dimensions.get("security")
        return d.hardcoded_secret_patterns if d else []

    def dimension_rule(self, dim: str) -> str:
        """Return the TH rule reference for a dimension."""
        d = self.dimensions.get(dim)
        return d.rule if d else "TH-02"

    def file_filter_keywords(self, check_type: str) -> Optional[List[str]]:
        """Return file-name filter keywords for a check_type, or None if unknown."""
        return self.file_filters.get(check_type)

    def phase_directory(self, phase: int) -> str:
        """Return the numbered directory name for a phase (e.g., '03-development')."""
        return self.phase_dir_map.get(phase, "docs")

    # ── serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "scoring": self.scoring.to_dict(),
            "phases": {str(k): v.to_dict() for k, v in self.phases.items()},
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "file_filters": self.file_filters,
            "phase_dir_map": self.phase_dir_map,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConstitutionProfile":
        phases = {}
        for k, v in d.get("phases", {}).items():
            phases[int(k)] = PhaseProfile.from_dict(v)

        dimensions = {}
        for k, v in d.get("dimensions", {}).items():
            dimensions[k] = DimensionProfile.from_dict(v)

        phase_dir_map = {}
        for k, v in d.get("phase_dir_map", {}).items():
            phase_dir_map[int(k)] = v

        return cls(
            scoring=ScoringProfile.from_dict(d.get("scoring", {})),
            phases=phases,
            dimensions=dimensions,
            file_filters=d.get("file_filters", {}),
            phase_dir_map=phase_dir_map,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    # ── merge ─────────────────────────────────────────────────────────────

    def merge(self, overrides: "ConstitutionProfile") -> "ConstitutionProfile":
        """Deep-merge overrides into self. Returns a NEW profile.

        Lists (keywords, patterns) are replaced, not appended.
        Dicts (phases, dimensions, file_filters) are merged key-by-key.
        """
        result = ConstitutionProfile(
            scoring=overrides.scoring if overrides.scoring.method != "min_of_dimensions" else self.scoring,
            phases=dict(self.phases),
            dimensions=dict(self.dimensions),
            file_filters=dict(self.file_filters),
            phase_dir_map=dict(self.phase_dir_map),
        )
        # merge phases
        for pk, pv in overrides.phases.items():
            if pk in result.phases:
                existing = result.phases[pk]
                result.phases[pk] = PhaseProfile(
                    active_dimensions=pv.active_dimensions or existing.active_dimensions,
                    composite_threshold=pv.composite_threshold if pv.composite_threshold is not None else existing.composite_threshold,
                )
            else:
                result.phases[pk] = pv
        # merge dimensions
        for dk, dv in overrides.dimensions.items():
            if dk in result.dimensions:
                existing_dim = result.dimensions[dk]
                result.dimensions[dk] = DimensionProfile(
                    threshold=dv.threshold if dv.threshold is not None else existing_dim.threshold,
                    keywords=dv.keywords or existing_dim.keywords,
                    rule=dv.rule or existing_dim.rule,
                    hardcoded_secret_patterns=dv.hardcoded_secret_patterns or existing_dim.hardcoded_secret_patterns,
                )
            else:
                result.dimensions[dk] = dv
        # merge file_filters
        for fk, fv in overrides.file_filters.items():
            result.file_filters[fk] = fv
        # merge phase_dir_map
        for dir_key, dir_val in overrides.phase_dir_map.items():
            result.phase_dir_map[dir_key] = dir_val
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Defaults — match current hardcoded values in runner.py + constitution/__init__.py
# ══════════════════════════════════════════════════════════════════════════════

def _build_defaults() -> ConstitutionProfile:
    return ConstitutionProfile(
        scoring=ScoringProfile(method="min_of_dimensions"),
        phases={
            1: PhaseProfile(active_dimensions=["correctness", "security"], composite_threshold=100.0),
            2: PhaseProfile(active_dimensions=["correctness", "security", "maintainability"], composite_threshold=100.0),
            3: PhaseProfile(active_dimensions=["correctness", "security", "maintainability"], composite_threshold=90.0),
            4: PhaseProfile(active_dimensions=["correctness", "security", "maintainability", "coverage"], composite_threshold=90.0),
            5: PhaseProfile(active_dimensions=["correctness", "security", "maintainability", "coverage"], composite_threshold=80.0),
            6: PhaseProfile(active_dimensions=["correctness", "security", "maintainability", "coverage"], composite_threshold=80.0),
            7: PhaseProfile(active_dimensions=["correctness", "security", "maintainability", "coverage"], composite_threshold=80.0),
            8: PhaseProfile(active_dimensions=["correctness", "security", "maintainability", "coverage"], composite_threshold=80.0),
        },
        dimensions={
            "correctness": DimensionProfile(
                threshold=100.0,
                rule="TH-03",
                keywords=[
                    "fr-", "nfr-", "acceptance criteria", "test case",
                    "### fr-", "## fr-", "requirement", "specification",
                    "traceability matrix", "srs", "sad",
                ],
            ),
            "security": DimensionProfile(
                threshold=100.0,
                rule="TH-04",
                keywords=[
                    "auth", "validation", "sanitize", "encrypt", "hmac",
                    "signature", "verify", "rbac", "permission", "token",
                    "pii", "mask", "secret", "whitelist", "tls",
                    "compare_digest", "input sanitizer", "rate limit",
                    "security", "vulnerability",
                ],
                hardcoded_secret_patterns=[
                    "password = \"", "password = '",
                    "secret_key = \"", "secret_key = '",
                    "api_key = \"", "api_key = '",
                    "token = \"", "token = '",
                ],
            ),
            "maintainability": DimensionProfile(
                threshold=90.0,
                rule="TH-05",
                keywords=[
                    "docstring", "type hint", "dataclass", "abc",
                    "interface", "module", "class", "def",
                    "import", "from", "snake_case", "PascalCase",
                ],
            ),
            "coverage": DimensionProfile(
                threshold=90.0,
                rule="TH-06",
                keywords=[
                    "test coverage", "pytest", "unit test", "integration test",
                    "mock", "fixture", "assert", "coverage report",
                    "test plan", "regression",
                ],
            ),
        },
        file_filters={
            "srs": ["srs", "spec", "requirement", "fr-", "nfr-"],
            "sad": ["sad", "architecture", "adr", "design"],
            "implementation": ["implementation", "compliance", "source"],
            "test_plan": ["test_plan", "test_report", "test_result"],
            "verification": ["verify", "verification", "baseline"],
            "quality_report": ["quality_report", "quality", "monitoring"],
            "risk_management": ["risk", "assessment", "risk_register"],
            "configuration": ["config", "release", "configuration"],
            "all": [],
        },
        phase_dir_map={
            1: "01-requirements",
            2: "02-architecture",
            3: "03-development",
            4: "04-testing",
            5: "05-verify",
            6: "06-quality",
            7: "07-risk",
            8: "08-config",
        },
    )


_DEFAULTS: Optional[ConstitutionProfile] = None


def defaults() -> ConstitutionProfile:
    """Return a fresh copy of the built-in defaults."""
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = _build_defaults()
    return copy.deepcopy(_DEFAULTS)


# ══════════════════════════════════════════════════════════════════════════════
# Loader
# ══════════════════════════════════════════════════════════════════════════════

def load_profile(
    path: Optional[str] = None,
    *,
    env: Optional[str] = None,
    overrides: Optional[dict] = None,
) -> ConstitutionProfile:
    """Load constitution profile with full override chain.

    Resolution order (last wins):
      1. Built-in defaults
      2. JSON file at `path` (default: .methodology/constitution_profile.json)
      3. .methodology/enforcement.json → constitution key (if present)
      4. Env var `env` (default: METHODOLOGY_CONSTITUTION_PROFILE) — JSON string
      5. `overrides` dict — programmatic overrides

    If no overrides are found, returns the built-in defaults (backward compatible).
    """
    profile = defaults()

    # 1. File (dedicated profile)
    resolved_path = path or ".methodology/constitution_profile.json"
    if os.path.exists(resolved_path):
        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                profile = profile.merge(ConstitutionProfile.from_dict(json.load(f)))
        except Exception:
            import warnings
            warnings.warn(f"Failed to load constitution profile from {resolved_path} — using defaults")

    # 2. enforcement.json → constitution key (if present)
    enforcement_path = ".methodology/enforcement.json"
    if os.path.exists(enforcement_path):
        try:
            with open(enforcement_path, "r", encoding="utf-8") as f:
                enforcement_data = json.load(f)
            constitution_override = enforcement_data.get("constitution")
            if constitution_override:
                profile = profile.merge(ConstitutionProfile.from_dict(constitution_override))
        except Exception:
            pass  # enforcement.json is optional; silent fallback

    # 3. Env var
    env_name = env or "METHODOLOGY_CONSTITUTION_PROFILE"
    env_val = os.environ.get(env_name)
    if env_val:
        try:
            profile = profile.merge(ConstitutionProfile.from_dict(json.loads(env_val)))
        except Exception:
            import warnings
            warnings.warn(f"Failed to parse {env_name} — using current profile")

    # 4. Programmatic overrides
    if overrides:
        try:
            profile = profile.merge(ConstitutionProfile.from_dict(overrides))
        except Exception:
            import warnings
            warnings.warn("Failed to parse programmatic overrides — using current profile")

    return profile


# Module-level singleton — lazy-loaded on first access
_profile: Optional[ConstitutionProfile] = None


def get_profile() -> ConstitutionProfile:
    """Return the module-level singleton profile (lazy-loaded)."""
    global _profile
    if _profile is None:
        _profile = load_profile()
    return _profile


def reset_profile() -> None:
    """Reset the module-level singleton (for testing)."""
    global _profile
    _profile = None

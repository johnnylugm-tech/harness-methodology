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
    """Per-phase configuration: which dimensions are active, the composite threshold,
    and optional per-dimension keyword overrides for phase-appropriate vocabulary."""

    active_dimensions: List[str] = field(default_factory=list)
    composite_threshold: Optional[float] = None
    dimension_keywords: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        result: dict = {
            "active_dimensions": self.active_dimensions,
            "composite_threshold": self.composite_threshold,
        }
        if self.dimension_keywords:
            result["dimension_keywords"] = self.dimension_keywords
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "PhaseProfile":
        return cls(
            active_dimensions=d.get("active_dimensions", []),
            composite_threshold=d.get("composite_threshold"),
            dimension_keywords=d.get("dimension_keywords", {}),
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

    def dimension_keywords_for_phase(self, dim: str, phase: Optional[int]) -> List[str]:
        """Return the keyword set for a dimension at a specific phase.

        When phase is None, returns global keywords.
        Otherwise checks per-phase overrides first, falls back to global.
        """
        if phase is None:
            return self.dimension_keywords(dim)
        p = self.phases.get(phase)
        if p and dim in p.dimension_keywords:
            return p.dimension_keywords[dim]
        return self.dimension_keywords(dim)

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
                merged_kw = dict(existing.dimension_keywords)
                merged_kw.update(pv.dimension_keywords)
                result.phases[pk] = PhaseProfile(
                    active_dimensions=pv.active_dimensions or existing.active_dimensions,
                    composite_threshold=pv.composite_threshold if pv.composite_threshold is not None else existing.composite_threshold,
                    dimension_keywords=merged_kw,
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
# Typed Config Schemas — dataclass-based gate/phase/dimension configuration
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PhaseConfig:
    """Typed configuration for a single phase."""
    phase_num: int
    name: str
    entry_score: int | None = None
    exit_gate: int | None = None
    key_artifact: str = ""
    per_fr_gate1: bool = False


@dataclass
class DimensionConfig:
    """Typed configuration for a single quality dimension."""
    name: str
    threshold: float
    tier: int = 1
    weight: float = 1.0


@dataclass
class GateConfig:
    """Typed configuration for a quality gate, loaded from YAML."""
    gate_num: int
    score_gate: float
    dimensions: list[DimensionConfig]
    per_dim_min: float | None = None
    max_rounds: int = 3
    blocking: bool = True
    trigger: str = ""
    scope: str = ""
    crg: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Backward-compatible dict representation."""
        return {
            "gate_num": self.gate_num,
            "score_gate": self.score_gate,
            "dimensions": [
                {"name": d.name, "threshold": d.threshold, "tier": d.tier, "weight": d.weight}
                for d in self.dimensions
            ],
            "per_dim_min": self.per_dim_min,
            "max_rounds": self.max_rounds,
            "blocking": self.blocking,
            "trigger": self.trigger,
            "scope": self.scope,
            "crg": self.crg,
        }

    @classmethod
    def from_dict(cls, raw: dict, gate_num: int) -> "GateConfig":
        """Parse a GateConfig from a raw YAML dict."""
        dims = []
        for d in raw.get("dimensions", []):
            dims.append(DimensionConfig(
                name=d.get("name", ""),
                threshold=float(d.get("threshold", 75)),
                tier=int(d.get("tier", 1)),
                weight=float(d.get("weight", 1.0)),
            ))
        return cls(
            gate_num=gate_num,
            score_gate=float(raw.get("score_gate", raw.get("gate", 75))),
            dimensions=dims,
            per_dim_min=float(raw["per_dim_min"]) if raw.get("per_dim_min") is not None else None,
            max_rounds=int(raw.get("max_rounds", 3)),
            blocking=bool(raw.get("blocking", True)),
            trigger=str(raw.get("trigger", "")),
            scope=str(raw.get("scope", "")),
            crg=dict(raw.get("crg", {})),
        )


def _phase_configs() -> dict[int, PhaseConfig]:
    """Built-in per-phase configurations."""
    return {
        1: PhaseConfig(phase_num=1, name="Requirements", entry_score=None, exit_gate=None, key_artifact="SRS.md", per_fr_gate1=False),
        2: PhaseConfig(phase_num=2, name="Architecture", entry_score=None, exit_gate=None, key_artifact="SAD.md", per_fr_gate1=False),
        3: PhaseConfig(phase_num=3, name="Implementation", entry_score=75, exit_gate=2, key_artifact="03-development/src/", per_fr_gate1=True),
        4: PhaseConfig(phase_num=4, name="Testing", entry_score=75, exit_gate=3, key_artifact="TEST_RESULTS.md", per_fr_gate1=True),
        5: PhaseConfig(phase_num=5, name="Verification", entry_score=80, exit_gate=None, key_artifact="VERIFICATION_REPORT.md", per_fr_gate1=True),
        6: PhaseConfig(phase_num=6, name="Quality", entry_score=85, exit_gate=4, key_artifact="QUALITY_REPORT.md", per_fr_gate1=False),
        7: PhaseConfig(phase_num=7, name="Risk", entry_score=85, exit_gate=None, key_artifact="RISK_ASSESSMENT.md", per_fr_gate1=True),
        8: PhaseConfig(phase_num=8, name="Configuration", entry_score=85, exit_gate=None, key_artifact="CONFIG_RECORDS.md", per_fr_gate1=True),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Defaults — match current hardcoded values in runner.py + constitution/__init__.py
# ══════════════════════════════════════════════════════════════════════════════

def _build_defaults() -> ConstitutionProfile:
    # Per-phase correctness keyword overrides for non-SRS phases (P5-P8).
    # Global correctness keywords (below) are SRS/SAD-centric; documents in
    # later phases reference FRs but use phase-appropriate vocabulary.
    # Each override completely replaces the global keyword set for that phase.
    _p5_correctness_kw = [
        "fr-", "nfr-", "acceptance criteria", "requirement", "specification",
        "verify", "verification", "traceability", "baseline",
    ]
    _p6_correctness_kw = [
        "fr-", "nfr-", "requirement", "specification",
        "quality", "monitoring", "audit", "completeness", "coverage",
    ]
    _p7_correctness_kw = [
        "fr-", "nfr-", "requirement", "specification",
        "risk", "mitigation", "vulnerability", "assessment", "threat", "security",
    ]
    _p8_correctness_kw = [
        "fr-", "nfr-", "requirement", "specification",
        "config", "configuration", "deployment", "release", "environment",
        "rollback", "secret",
    ]

    return ConstitutionProfile(
        scoring=ScoringProfile(method="min_of_dimensions"),
        phases={
            1: PhaseProfile(active_dimensions=["correctness", "security"], composite_threshold=100.0),
            2: PhaseProfile(active_dimensions=["correctness", "security", "maintainability"], composite_threshold=100.0),
            3: PhaseProfile(active_dimensions=["correctness", "security", "maintainability"], composite_threshold=90.0),
            4: PhaseProfile(active_dimensions=["correctness", "security", "maintainability", "coverage"], composite_threshold=90.0),
            # P5 (Verification): correctness + security only.
            # Preemptive change (not empirically driven): applies the same reasoning as
            # P7 (where RISK_REGISTER.md failures were observed). If a VERIFICATION_REPORT.md
            # happens to embed code snippets containing docstring/pytest references,
            # maintainability/coverage signal would be lost — but the common case for a
            # verification summary doc has no such vocabulary.
            # - maintainability excluded: keywords (docstring, type hint, snake_case)
            #   are code-centric and do not appear in verification reports.
            # - coverage excluded: its keywords (pytest, mock, assert) are test-centric;
            #   FR→test traceability is checked separately by preflight_traceability.
            # - composite_threshold=65: with per-phase correctness keywords (verify,
            #   verification, baseline), well-written verification reports pass.
            5: PhaseProfile(
                active_dimensions=["correctness", "security"],
                composite_threshold=65.0,
                dimension_keywords={"correctness": _p5_correctness_kw},
            ),
            # P6 (Quality): correctness + security only.
            # Preemptive change (not empirically driven): applies the same reasoning as
            # P7 (where RISK_REGISTER.md failures were observed). A QUALITY_REPORT.md
            # may reference pytest/mock in embedded code snippets — if so, the coverage
            # dimension would provide signal that is now excluded. Restore coverage if
            # real quality reports routinely include such references.
            # - maintainability excluded: keywords are code-centric.
            # - coverage excluded: quality reports discuss monitoring/audit/completeness
            #   but not pytest/mock/fixture; those are checked by traceability.
            # - composite_threshold=65: with per-phase correctness keywords (quality,
            #   monitoring, audit, completeness), well-written quality reports pass.
            6: PhaseProfile(
                active_dimensions=["correctness", "security"],
                composite_threshold=65.0,
                dimension_keywords={"correctness": _p6_correctness_kw},
            ),
            # P7 (Risk Management): correctness + security only.
            # - maintainability excluded: code-centric keywords inapplicable to risk docs.
            # - coverage excluded: test-centric keywords inapplicable to risk registers.
            # - composite_threshold=65 (not 80): with per-phase correctness keywords
            #   (risk, mitigation, vulnerability, assessment), well-written risk docs pass.
            7: PhaseProfile(
                active_dimensions=["correctness", "security"],
                composite_threshold=65.0,
                dimension_keywords={"correctness": _p7_correctness_kw},
            ),
            # P8 (Configuration Management): correctness + security only.
            # Empirically driven: CONFIG_RECORDS.md + RELEASE_CHECKLIST.md failed
            # constitution at 4 dims / 80 threshold. Configuration documents contain
            # FR references and security vocabulary but no code/test keywords.
            # - maintainability excluded: code-centric keywords inapplicable to config docs.
            # - coverage excluded: test-centric keywords inapplicable to config records.
            # - composite_threshold=65: with per-phase correctness keywords (config,
            #   deployment, release, environment, rollback), well-written config docs pass.
            8: PhaseProfile(
                active_dimensions=["correctness", "security"],
                composite_threshold=65.0,
                dimension_keywords={"correctness": _p8_correctness_kw},
            ),
        },
        dimensions={
            "correctness": DimensionProfile(
                threshold=100.0,
                rule="TH-03",
                # Global correctness keywords for P1-P4 (SRS/SAD-centric).
                # ### fr- and ## fr- removed — redundant with fr- (substring match).
                # test case removed — P4 activity, phase-inappropriate for P1-P3.
                keywords=[
                    "fr-", "nfr-", "acceptance criteria", "requirement",
                    "specification", "traceability matrix", "srs", "sad",
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
                    # Testing / code documents (P3-P4): traditional coverage terms
                    "test coverage", "pytest", "unit test", "integration test",
                    "mock", "fixture", "assert", "coverage report",
                    "test plan", "regression",
                    # Risk / quality / config documents (P5-P8): domain coverage terms.
                    # "Coverage" in risk management = completeness of risk identification
                    # and mitigation; in config = completeness of deployment/audit coverage.
                    # These keywords appear naturally in well-written phase docs without
                    # needing to be artificially inserted.
                    "coverage", "mitigation", "monitoring",
                    "audit", "completeness",
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
            5: "05-verification",
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

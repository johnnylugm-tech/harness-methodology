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
import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
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

    def to_dict(self) -> dict:
        return {
            "threshold": self.threshold,
            "keywords": self.keywords,
            "rule": self.rule,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DimensionProfile":
        return cls(
            threshold=d.get("threshold"),
            keywords=d.get("keywords", []),
            rule=d.get("rule", "TH-02"),
        )


@dataclass
class PhaseProfile:
    """Per-phase configuration: which dimensions are active, the composite threshold,
    optional per-dimension keyword overrides, and per-phase file exclusion patterns."""

    active_dimensions: List[str] = field(default_factory=list)
    composite_threshold: Optional[float] = None
    dimension_keywords: Dict[str, List[str]] = field(default_factory=dict)
    exclude_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        result: dict = {
            "active_dimensions": self.active_dimensions,
            "composite_threshold": self.composite_threshold,
        }
        if self.dimension_keywords:
            result["dimension_keywords"] = self.dimension_keywords
        if self.exclude_patterns:
            result["exclude_patterns"] = self.exclude_patterns
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "PhaseProfile":
        return cls(
            active_dimensions=d.get("active_dimensions", []),
            composite_threshold=d.get("composite_threshold"),
            dimension_keywords=d.get("dimension_keywords", {}),
            exclude_patterns=d.get("exclude_patterns", []),
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
    exclude_patterns: List[str] = field(default_factory=list)

    # ── helpers ───────────────────────────────────────────────────────────

    def is_excluded(self, file_path: Path, phase: Optional[int] = None) -> bool:
        """Check whether *file_path* matches any global or per-phase exclusion pattern.

        Patterns use fnmatch against the file basename (e.g. ``HANDOVER.md``,
        ``*STAGE_PASS.md``).  Glob-style wildcards (``*``, ``?``, ``[seq]``) are
        supported per :func:`fnmatch.fnmatch`.
        """
        file_name = file_path.name
        # Global patterns
        for pat in self.exclude_patterns:
            if fnmatch.fnmatch(file_name, pat):
                return True
        # Per-phase patterns
        if phase is not None:
            p = self.phases.get(phase)
            if p:
                for pat in p.exclude_patterns:
                    if fnmatch.fnmatch(file_name, pat):
                        return True
        return False

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

    def dimension_rule(self, dim: str) -> str:
        """Return the TH rule reference for a dimension."""
        d = self.dimensions.get(dim)
        return d.rule if d else "TH-02"

    def file_filter_keywords(self, check_type: str) -> Optional[List[str]]:
        """Return file-name filter keywords for a check_type, or None if unknown."""
        return self.file_filters.get(check_type)


    # ── serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        result: dict = {
            "scoring": self.scoring.to_dict(),
            "phases": {str(k): v.to_dict() for k, v in self.phases.items()},
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
            "file_filters": self.file_filters,
        }
        if self.exclude_patterns:
            result["exclude_patterns"] = self.exclude_patterns
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "ConstitutionProfile":
        phases = {}
        for k, v in d.get("phases", {}).items():
            phases[int(k)] = PhaseProfile.from_dict(v)

        dimensions = {}
        for k, v in d.get("dimensions", {}).items():
            dimensions[k] = DimensionProfile.from_dict(v)

        return cls(
            scoring=ScoringProfile.from_dict(d.get("scoring", {})),
            phases=phases,
            dimensions=dimensions,
            file_filters=d.get("file_filters", {}),
            exclude_patterns=d.get("exclude_patterns", []),
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
                    exclude_patterns=pv.exclude_patterns or existing.exclude_patterns,
                )
            else:
                result.phases[pk] = pv
        # merge global exclude_patterns (preserve order, don't duplicate)
        if overrides.exclude_patterns:
            seen = set(result.exclude_patterns)
            result.exclude_patterns = result.exclude_patterns + [
                p for p in overrides.exclude_patterns if p not in seen
            ]
        # merge dimensions
        for dk, dv in overrides.dimensions.items():
            if dk in result.dimensions:
                existing_dim = result.dimensions[dk]
                result.dimensions[dk] = DimensionProfile(
                    threshold=dv.threshold if dv.threshold is not None else existing_dim.threshold,
                    keywords=dv.keywords or existing_dim.keywords,
                    rule=dv.rule or existing_dim.rule,
                )
            else:
                result.dimensions[dk] = dv
        # merge file_filters
        for fk, fv in overrides.file_filters.items():
            result.file_filters[fk] = fv
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
    # Per-phase correctness/security keyword overrides for all phases (P1-P8).
    # Global keyword lists (defined in dimensions below) serve as fallback for
    # phase=None calls. Every phase now has phase-appropriate overrides.
    # Each override completely replaces the global keyword set for that phase.

    # P1 (Requirements): requirements-phase keyword overrides.
    # Empirically driven: SRS/SPEC_TRACKING/TRACEABILITY_MATRIX failed
    # constitution at 100% threshold with global keyword sets.
    # - correctness: removes "sad" (P2 artifact, not referenced in P1 docs)
    #   and replaces "traceability matrix" (exact phrase) with "traceability"
    #   (substring match covers TRACEABILITY_MATRIX.md heading naturally).
    #   "acceptance criteria" is kept: SRS documents define acceptance criteria
    #   at the requirements phase (P4 runs the tests; P1 defines the criteria).
    # - security dimension REMOVED from P1 (2026-06-12, integration-test E2E):
    #   a security topic-keyword checklist is project-shape-dependent — an
    #   honest SRS for a local CLI never mentions tls/rbac/pii/encrypt, so the
    #   dimension was unsatisfiable in practice. Corpus evidence with the
    #   12-keyword P1 list: tts-new (completed P1-P8) scored 36%, taskq
    #   (Agent-B-approved SRS) scored 50% — both blocked at min-composite 75.
    #   Requirements-phase security adequacy is owned by Agent B review (NFR
    #   completeness vs brief), SAB NFR→dimension floors, and P3+ security
    #   tooling (bandit/semgrep/gitleaks); a keyword density gate cannot
    #   distinguish "SRS ignored security" from "project has no TLS surface".
    # - composite_threshold=75: a well-written SRS must cover 75% of the
    #   requirements-appropriate keyword set; 100% was unachievable because
    #   the global list mixed implementation and requirements vocabulary.
    _p1_correctness_kw = [
        "fr-", "nfr-", "acceptance criteria", "requirement",
        "specification", "traceability", "srs",
    ]

    # P2 (Architecture: SAD.md, ADR.md)
    # - correctness: removes "acceptance criteria" (testing concept, not in architecture docs)
    # - security dimension REMOVED from P2 (2026-06-12, integration-test E2E,
    #   same defect class as P1): 15-topic keyword density (rbac/tls/pii/
    #   rate limit/encrypt/signature…) is project-shape-dependent. Corpus:
    #   tts-new (completed P1-P8, web service) scored 69%, taskq (local CLI,
    #   Agent-B-approved SAD+ADR, 89% holistic audit) scored 31% — both
    #   blocked at min-composite 80. Architecture-phase security adequacy is
    #   owned by Agent B TECH_LEAD review (SAD vs security NFRs), the SAB
    #   NFR→dimension floors (M2+), Gate 2-4 tool-scored security dims
    #   (bandit/semgrep on real code), and v2.9 B1 architecture-risk triggers
    #   in TEST_SPEC derivation.
    # - maintainability dropped: SAD/ADR are Markdown docs — code vocabulary (class, def,
    #   docstring, type hint, snake_case) never appears; would cap score at 0%.
    # - composite_threshold=80: architecture docs must cover the structural vocabulary
    _p2_correctness_kw = [
        "fr-", "nfr-", "requirement", "specification",
        "traceability matrix", "srs", "sad",
    ]

    # P3 (Implementation: source code files)
    # - correctness: reduced to FR-reference vocabulary only; source code comments reference
    #   FR/NFR IDs but never contain "acceptance criteria", "traceability matrix", "srs", "sad".
    # - security: removes whitelist (deprecated term), compare_digest (Python stdlib internals),
    #   input sanitizer (exact two-word phrase unlikely in code).
    # - maintainability: kept — source code naturally has class/def/import/docstring patterns.
    # - composite_threshold=80 (was 90): not every project uses all security primitives.
    _p3_correctness_kw = [
        "fr-", "nfr-", "requirement", "specification",
    ]
    _p3_security_kw = [
        "auth", "validation", "sanitize", "encrypt",
        "hmac", "signature", "verify", "rbac", "permission",
        "token", "pii", "mask", "secret", "tls",
        "rate limit", "security", "vulnerability",
    ]

    # P4 (Testing: test files, TEST_RESULTS.md)
    # - correctness: adds "test case" (P4-specific activity; excluded from global set as
    #   phase-inappropriate for P1-P3) and "acceptance criteria" (acceptance testing phase).
    #   Removes "traceability matrix", "srs", "sad" — test code doesn't reference these.
    # - security: same reduction as P2 — test code exercises auth/permission/token patterns
    #   but not cryptographic primitives (hmac, mask, whitelist, compare_digest, input sanitizer).
    # - maintainability + coverage: both kept — test code has class/def/pytest/mock patterns.
    # - composite_threshold=80 (was 90): test suites target behavioral coverage, not all
    #   security primitives.
    _p4_correctness_kw = [
        "fr-", "nfr-", "requirement", "specification",
        "acceptance criteria", "test case",
    ]
    _p4_security_kw = [
        "auth", "validation", "sanitize", "encrypt",
        "signature", "verify", "rbac", "permission",
        "token", "pii", "secret", "tls",
        "rate limit", "security", "vulnerability",
    ]

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

    # Meta-documents excluded from constitution keyword scans.
    # These files are operational/tracking/table documents — not prose
    # deliverables. They inherently contain few or zero constitution keywords
    # and would dilute the aggregate keyword density below the phase threshold.
    #
    # Excluded by nature:
    #   HANDOVER.md              — operational handover log
    #   *STAGE_PASS.md           — auto-generated gate certificates
    #   SPEC_TRACKING.md         — spec tracking table (tabular, not prose)
    #   TRACEABILITY_MATRIX.md   — traceability matrix (tabular, not prose)
    #   TEST_INVENTORY.yaml      — YAML test inventory (not prose)
    #   TEST_SPEC.md             — test spec table (tabular, not prose)
    #
    # Configurable via .methodology/constitution_profile.json → exclude_patterns.
    _META_EXCLUDE = [
        "HANDOVER.md",
        "*STAGE_PASS.md",
        "SPEC_TRACKING.md",
        "TRACEABILITY_MATRIX.md",
        "TEST_INVENTORY.yaml",
        "TEST_SPEC.md",
    ]

    return ConstitutionProfile(
        scoring=ScoringProfile(method="min_of_dimensions"),
        exclude_patterns=_META_EXCLUDE,
        phases={
            1: PhaseProfile(
                active_dimensions=["correctness"],
                composite_threshold=75.0,
                dimension_keywords={
                    "correctness": _p1_correctness_kw,
                },
            ),
            2: PhaseProfile(
                active_dimensions=["correctness"],
                composite_threshold=80.0,
                dimension_keywords={
                    "correctness": _p2_correctness_kw,
                },
            ),
            3: PhaseProfile(
                # Bug #35 fix (deeper): P3+ implementation phases have
                # mostly Python source. The per-file keyword density check
                # is designed for markdown-heavy phases; for code-only
                # phases the required security/maintainability keywords
                # (auth, rbac, tls, snake_case, ...) don't naturally appear,
                # permanently capping scores at 10-30%. The authoritative
                # quality signal for implementation is the framework AST +
                # independent tool scores (Gate 1/2 coverage, mutation,
                # linting, type safety) — not the keyword density proxy.
                # Drop security/maintainability (P5+ applies the same
                # reasoning for non-code phases); keep correctness to
                # verify FR/NFR traceability references in docstrings.
                # Threshold 30: a well-implemented project with FR/NFR
                # docstring references (~50% keyword coverage) can pass;
                # a hollow/empty P3 dir still scores 0%.
                active_dimensions=["correctness"],
                composite_threshold=30.0,
                dimension_keywords={
                    "correctness": _p3_correctness_kw,
                },
            ),
            4: PhaseProfile(
                # Bug #35 extension (P3 fix was at 07ef908): P4 is also Python
                # test/code-heavy. Per-file keyword density for security/
                # maintainability/coverage is meaningless for .py files —
                # auth/tls/docstring/pytest keywords don't appear naturally
                # in code. The authoritative quality signal is the AST +
                # independent tool scores from Gate 3.
                active_dimensions=["correctness"],
                composite_threshold=30.0,
                dimension_keywords={
                    "correctness": _p4_correctness_kw,
                },
            ),
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

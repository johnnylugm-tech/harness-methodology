#!/usr/bin/env python3
"""
phase_auditor.py -- harness-methodology v6.49 Phase Audit Engine
============================================================
Auditor perspective: access only the artifacts from a single GitHub phase,
independently verify a Phase claimed as passed by an AI Agent, and output a final audit report.

Usage:
    python phase_auditor.py --repo johnnylugm-tech/tts-kokoro-v613 --phase 1
    python phase_auditor.py --repo OWNER/REPO --phase 2 --methodology-version v6.49

Required arguments (project_context):
    --repo          GitHub repo (owner/repo)           [required]
    --phase         Phase number to audit (1-8)        [required]
    --branch        Target branch (default: main)      [optional]
    --project-name  Project display name                [optional, inferred from repo]
    --methodology-version  v6.13 (default)             [optional]
"""

import argparse
import base64
import json
import re
import subprocess  # nosec B404
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote


# ─────────────────────────────────────────────
# 1. METHODOLOGY-V2 v6.13 RULE LIBRARY (hardcoded, no remote framework dependency)
# ─────────────────────────────────────────────

HARD_RULES = {
    "HR-01": "A/B must be different Agents; self-review is forbidden",
    "HR-02": "Quality Gate must include actual command output",
    "HR-03": "Phases must be executed in order; skipping is forbidden",
    "HR-07": "DEVELOPMENT_LOG must record session_id",
    "HR-08": "Quality Gate must be executed at the end of every Phase",
    "HR-09": "Claims Verifier verification must pass",
    "HR-10": ".methodology/sessions_spawn.log must exist and contain A/B records",
    "HR-11": "Phase Truth score < 90% blocks entry to next Phase",
}

# Gate entry requirements: phase → required gate number that must have PASS
# (mirrors _ENTRY_GATE_MAP in harness_cli.py)
_ENTRY_GATE_MAP: dict[int, int] = {4: 2, 5: 3, 6: 3, 7: 4, 8: 4}

# Minimum numeric score for each exit gate (from framework spec)
# Gate 2 = P3 exit (≥40%), Gate 3 = P4 exit (≥70%), Gate 4 = P6 QA (≥88%)
_GATE_SCORE_THRESHOLDS: dict[int, float] = {2: 40.0, 3: 70.0, 4: 88.0}

# Milestone commit requirements per phase (absorbed from phase_end_audit._MILESTONES)
_PHASE_MILESTONES: dict[int, list[str]] = {
    3: ["p3-mid", "p3-pre-gate2"],
    4: ["p4-mid", "p4-pre-gate3"],
    5: ["p5-baseline"],
    7: ["p7"],
    8: ["p8"],
}

# Phase specifications (per SKILL.md v6.13 Phase routing table)
PHASE_SPEC: dict[int, dict[str, Any]] = {
    1: {
        "name": "Requirements Specification",
        "agent_a": "requirements_engineer",
        "agent_b": "business_analyst",
        "ab_rounds": 1,
        "constitution_type": "srs",
        # required deliverables: (candidate paths, description, is_mandatory)
        "deliverables": [
            (["01-requirements/SRS.md"],
             "SRS.md -- Software Requirements Specification", True),
            (["01-requirements/SPEC_TRACKING.md"],
             "SPEC_TRACKING.md -- Specification Tracking Table", True),
            (["01-requirements/TRACEABILITY_MATRIX.md"],
             "TRACEABILITY_MATRIX.md -- Traceability Matrix", True),
            (["DEVELOPMENT_LOG.md"],
             "DEVELOPMENT_LOG.md -- Development Log", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"],
             ".methodology/sessions_spawn.log -- A/B session records", True),
            (["TEST_INVENTORY.yaml"],
             "TEST_INVENTORY.yaml -- Test Specification Inventory", True),
            (["00-summary/Phase1_STAGE_PASS.md"],
             "Phase1_STAGE_PASS.md -- Phase pass certificate", False),
        ],
        "thresholds": {
            "TH-01": ("ASPICE Compliance Rate", ">80%"),
            "TH-03": ("Constitution Correctness", "=100%"),
            "TH-14": ("Spec Completeness", "=100%"),
        },
        # minimum FR count in SRS
        "min_fr_count": 3,
        # required SRS section keywords
        "srs_required_sections": ["Functional Requirements", "FR-", "Logic Verification Method"],
        # required SPEC_TRACKING columns
        "spec_tracking_required_cols": ["FR", "Description", "Status"],
        # required TRACEABILITY columns (Phase 1 init only; module column may be 'TBD')
        "traceability_required_cols": ["FR", "Module"],
        # minimum reasonable execution time (minutes)
        "min_duration_minutes": 5,
    },
    2: {
        "name": "Architecture Design",
        "agent_a": "architect",
        "agent_b": "tech_lead",
        "ab_rounds": 1,
        "constitution_type": "sad",
        "deliverables": [
            (["02-architecture/SAD.md"],
             "SAD.md -- System Architecture Document", True),
            (["02-architecture/adr/"],
             "ADR -- Architecture Decision Records", False),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase2_STAGE_PASS.md"],
             "Phase2_STAGE_PASS.md -- Phase pass certificate", False),
        ],
        "thresholds": {
            "TH-01": ("ASPICE Compliance Rate", ">80%"),
            "TH-03": ("Constitution Correctness", "=100%"),
            "TH-05": ("Constitution Maintainability", ">90%"),
        },
        "min_duration_minutes": 10,
    },
    3: {
        "name": "Code Implementation",
        "agent_a": "developer",
        "agent_b": "reviewer",
        "ab_rounds": -1,  # one round per module
        "constitution_type": "implementation",
        "deliverables": [
            (["03-development/src"],
             "src/ -- Source code directory", True),
            (["03-development/tests/"],
             "tests/ -- Unit tests", True),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase3_STAGE_PASS.md"],
             "Phase3_STAGE_PASS.md", True),
        ],
        "thresholds": {
            "TH-10": ("Test Pass Rate", "=100%"),
            "TH-11": ("Unit Test Coverage", "≥70%"),
        },
        "min_duration_minutes": 30,
    },
    4: {
        "name": "Testing",
        "agent_a": "qa",
        "agent_b": "reviewer",
        "ab_rounds": 2,
        "constitution_type": "test_plan",
        "deliverables": [
            (["04-testing/TEST_PLAN.md"],
             "TEST_PLAN.md", True),
            (["04-testing/TEST_RESULTS.md"],
             "TEST_RESULTS.md", True),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase4_STAGE_PASS.md"],
             "Phase4_STAGE_PASS.md", True),
        ],
        "thresholds": {
            "TH-10": ("Test Pass Rate", "=100%"),
            "TH-12": ("Unit Test Coverage", "≥80%"),
        },
        "min_duration_minutes": 10,
    },
    5: {
        "name": "Verification & Delivery",
        "agent_a": "devops",
        "agent_b": "architect",
        "ab_rounds": 2,
        "constitution_type": None,
        "deliverables": [
            (["05-verification/BASELINE.md"],
             "BASELINE.md (7 sections)", True),
            (["05-verification/VERIFICATION_REPORT.md"],
             "VERIFICATION_REPORT.md", True),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase5_STAGE_PASS.md"],
             "Phase5_STAGE_PASS.md", True),
        ],
        "thresholds": {
            "TH-02": ("Constitution Total Score", "≥80%"),
            "TH-07": ("Logic Correctness Score", ">=90"),
        },
        "min_duration_minutes": 15,
    },
    6: {
        "name": "Quality Assurance",
        "agent_a": "qa",
        "agent_b": "architect",
        "ab_rounds": 1,
        "constitution_type": None,
        "deliverables": [
            (["06-quality/QUALITY_REPORT.md"],
             "QUALITY_REPORT.md (7 sections)", True),
            (["RELEASE_NOTES.md"],
             "RELEASE_NOTES.md -- Release Notes", True),
            (["FINAL_SIGN_OFF.md"],
             "FINAL_SIGN_OFF.md -- Final Sign-off Document", True),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase6_STAGE_PASS.md"],
             "Phase6_STAGE_PASS.md", True),
        ],
        "thresholds": {
            "TH-02": ("Constitution Total Score", "≥80%"),
            "TH-07": ("Logic Correctness Score", ">=90"),
        },
        "min_duration_minutes": 10,
    },
    7: {
        "name": "Risk Management",
        "agent_a": "qa",
        "agent_b": "architect",
        "ab_rounds": 1,
        "constitution_type": None,
        "deliverables": [
            (["07-risk/RISK_STATUS_REPORT.md", "07-risk/RISK_ASSESSMENT.md"],
             "RISK_STATUS_REPORT.md / RISK_ASSESSMENT.md -- Risk Status Report", True),
            (["07-risk/RISK_REGISTER.md"],
             "RISK_REGISTER.md", True),
            (["07-risk/RISK_MITIGATION_PLANS.md"],
             "RISK_MITIGATION_PLANS.md -- Risk Mitigation Plans", True),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase7_STAGE_PASS.md"],
             "Phase7_STAGE_PASS.md", True),
        ],
        "thresholds": {
            "TH-07": ("Logic Correctness Score", ">=90"),
        },
        "min_duration_minutes": 10,
    },
    8: {
        "name": "Configuration Management",
        "agent_a": "devops",
        "agent_b": "architect",
        "ab_rounds": 1,
        "constitution_type": None,
        "deliverables": [
            (["08-config/CONFIG_RECORDS.md"],
             "CONFIG_RECORDS.md (8 sections)", True),
            (["08-config/RELEASE_CHECKLIST.md"],
             "RELEASE_CHECKLIST.md -- Release Validation Checklist", True),
            (["DEVELOPMENT_LOG.md"], "DEVELOPMENT_LOG.md", True),
            ([".methodology/sessions_spawn.log", "sessions_spawn.log"], ".methodology/sessions_spawn.log", True),
            (["00-summary/Phase8_STAGE_PASS.md"],
             "Phase8_STAGE_PASS.md", True),
        ],
        "thresholds": {},
        "min_duration_minutes": 10,
    },
}

# DEVELOPMENT_LOG quality keywords: valid QG output must contain at least one of these patterns
QG_EVIDENCE_PATTERNS = [
    r"Constitution.*?[\d.]+%",
    r"Compliance Rate.*?[\d.]+%",
    r"ASPICE.*?(?:PASS|FAIL|✅|❌)",
    r"pytest.*?(?:passed|failed|error)",
    r"coverage.*?[\d]+%",
    r"stage.pass.*?(?:\d+)/100",
    r"phase.verify.*?(?:PASS|FAIL|[\d]+%)",
    r"enforce.*?(?:BLOCK|PASS|0.*?violation)",
    r"Constitution Score.*?[\d.]+",
]

# DEVELOPMENT_LOG fake-pass detection (disallow these vague markers without actual output)
FAKE_PASS_PATTERNS = [
    r"^[\u2705\u2713]\s*(?:Passed|PASS|Done|pass|done)\s*$",
    r"^[\u2705\u2713]\s*Phase\s*\d+\s*(?:Done|PASS|Passed)\s*$",
]

# Required sections in STAGE_PASS (machine-generated by finalize-gate since v2.5.0)
STAGE_PASS_REQUIRED_SECTIONS = [
    "Gate Score",
    "Quality Status",
    "Deliverables",
]

# STAGE_PASS Agent B required keywords
STAGE_PASS_AGENT_B_KEYWORDS = [
    "APPROVE", "reviewer", "verdict", "review", "✅ APPROVE"
]


# ─────────────────────────────────────────────
# 2. DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass
class Finding:
    """Single audit finding"""
    check_id: str          # e.g. "C1-01"
    dimension: str         # e.g. "Deliverable Completeness"
    severity: str          # CRITICAL / WARNING / INFO / PASS
    title: str
    detail: str
    evidence: str = ""     # evidence snippet extracted from file
    rule_ref: str = ""     # corresponding HR-XX or TH-XX rule


@dataclass
class AuditResult:
    """Complete audit result"""
    repo: str
    phase: int
    phase_name: str
    audit_time: str
    findings: list[Finding] = field(default_factory=list)
    score: float = 0.0
    verdict: str = "PENDING"  # PASS / CONDITIONAL_PASS / FAIL

    def add(self, finding: Finding):
        """Record a finding in the audit result."""
        self.findings.append(finding)

    def criticals(self):
        """Return all CRITICAL severity findings."""
        return [f for f in self.findings if f.severity == "CRITICAL"]

    def warnings(self):
        """Return all WARNING severity findings."""
        return [f for f in self.findings if f.severity == "WARNING"]

    def passes(self):
        """Return all PASS/INFO findings."""
        return [f for f in self.findings if f.severity == "PASS"]


# ─────────────────────────────────────────────
# 3. GITHUB API ACCESS LAYER
# ─────────────────────────────────────────────

class GitHubFetcher:
    """Access GitHub Repo via gh CLI (no token env var required)"""

    def __init__(self, repo: str, branch: str = "main"):
        """Initialize fetcher for a GitHub repo and branch."""
        self.repo = repo
        self.branch = branch
        self._tree: Optional[list[dict]] = None
        self._file_cache: dict[str, Optional[str]] = {}

    def _gh(self, endpoint: str) -> Any:
        """Execute gh api command"""
        result = subprocess.run(  # nosec B603 B607
            ["gh", "api", endpoint],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def get_tree(self) -> list[dict]:
        """Get the full file tree of the repo"""
        if self._tree is not None:
            return self._tree
        data = self._gh(
            f"repos/{self.repo}/git/trees/{self.branch}?recursive=1"
        )
        if not data or "tree" not in data:
            self._tree = []
        else:
            self._tree = [
                item for item in data["tree"]
                if item.get("type") == "blob"
            ]
        return self._tree

    def file_exists(self, path: str) -> bool:
        tree = self.get_tree()
        return any(item["path"] == path for item in tree)

    def resolve_path(self, candidates: list[str]) -> Optional[str]:
        """Find the first existing path from a list of candidate paths"""
        for path in candidates:
            if self.file_exists(path):
                return path
        return None

    def get_file_content(self, path: str) -> Optional[str]:
        """Get file content (UTF-8 text)"""
        if path in self._file_cache:
            return self._file_cache[path]
        data = self._gh(
            f"repos/{self.repo}/contents/{quote(path, safe='/')}"
        )
        if not data or "content" not in data:
            self._file_cache[path] = None
            return None
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            self._file_cache[path] = content
            return content
        except Exception:
            self._file_cache[path] = None
            return None

    def get_commits(self, per_page: int = 30) -> list[dict]:
        """Get latest commits"""
        data = self._gh(
            f"repos/{self.repo}/commits?per_page={per_page}&sha={self.branch}"
        )
        return data if isinstance(data, list) else []

    def get_repo_info(self) -> dict:
        data = self._gh(f"repos/{self.repo}")
        return data or {}


class LocalFetcher:
    """Access local project filesystem (same interface as GitHubFetcher).

    Used when running audit-phase --project <path> directly on the project
    execution environment, without requiring GitHub access.
    """

    is_local: bool = True

    def __init__(self, project_root: str, branch: str = "main"):
        self.project_root = Path(project_root).resolve()
        self.repo = str(self.project_root)   # mirrors GitHubFetcher.repo
        self.branch = branch
        self._tree: Optional[list[dict]] = None
        self._file_cache: dict[str, Optional[str]] = {}

    def get_tree(self) -> list[dict]:
        """Walk local filesystem, excluding .git/ using path component check."""
        if self._tree is not None:
            return self._tree
        self._tree = []
        for p in self.project_root.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                rel = str(p.relative_to(self.project_root))
                self._tree.append({"path": rel, "type": "blob"})
        return self._tree

    def file_exists(self, path: str) -> bool:
        return (self.project_root / path.rstrip("/")).exists()

    def resolve_path(self, candidates: list[str]) -> Optional[str]:
        for path in candidates:
            if self.file_exists(path):
                return path
        return None

    def get_file_content(self, path: str) -> Optional[str]:
        if path in self._file_cache:
            return self._file_cache[path]
        full = self.project_root / path
        if not full.exists():
            self._file_cache[path] = None
            return None
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
            self._file_cache[path] = content
            return content
        except Exception:
            self._file_cache[path] = None
            return None

    def get_commits(self, per_page: int = 30) -> list[dict]:
        """Get commits via local git log (null-byte delimited to avoid false splits)."""
        result = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(self.project_root), "log",
             f"-{per_page}", "--format=%H%x00%ae%x00%s%x00%aI"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return []
        commits: list[dict] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\0")
            if len(parts) >= 4:
                commits.append({
                    "sha": parts[0],
                    "commit": {
                        "author": {"email": parts[1], "date": parts[3]},
                        "message": parts[2],
                    },
                })
        return commits

    def get_repo_info(self) -> dict:
        return {"name": self.project_root.name, "full_name": str(self.project_root)}

    def _is_git_tracked(self, rel_path: str) -> bool:
        """Return True if rel_path is tracked by git (committed at least once)."""
        result = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(self.project_root), "ls-files",
             "--error-unmatch", rel_path],
            capture_output=True
        )
        return result.returncode == 0


# ─────────────────────────────────────────────
# 4. AUDIT CHECKERS (per dimension)
# ─────────────────────────────────────────────

class PhaseAuditor:
    """Audits a single development phase against its deliverable/process spec.

    Validates 8 dimensions per phase: deliverables, stage-pass, session
    separation, dev log, content depth, commit timeline, claims crosscheck,
    and integrity. Produces an AuditResult with scored findings."""

    def __init__(self, fetcher: "GitHubFetcher | LocalFetcher", phase: int):
        self.gh = fetcher
        self.phase = phase
        self.spec: dict[str, Any] = PHASE_SPEC.get(phase, {})
        self.result = AuditResult(
            repo=fetcher.repo,
            phase=phase,
            phase_name=self.spec.get("name", f"Phase {phase}"),
            audit_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        # cache of resolved actual paths
        self._resolved: dict[str, Optional[str]] = {}

    def _resolve(self, candidates: list[str]) -> Optional[str]:
        """Resolve first-existing path from candidate list, cached."""
        key = candidates[0]
        if key not in self._resolved:
            self._resolved[key] = self.gh.resolve_path(candidates)
        return self._resolved[key]

    def _content(self, candidates: list[str]) -> Optional[str]:
        """Resolve path and fetch file content, cached."""
        path = self._resolve(candidates)
        if not path:
            return None
        return self.gh.get_file_content(path)

    # -- C1: Deliverable Completeness ----------------------------------────
    def check_c1_deliverables(self):
        """C1: Required deliverable existence check"""
        spec = self.spec
        for candidates, description, required in spec.get("deliverables", []):
            path = self._resolve(candidates)
            if path:
                self.result.add(Finding(
                    check_id="C1",
                    dimension="Deliverable Completeness",
                    severity="PASS",
                    title=f"✅ {description}",
                    detail=f"Found: {path}",
                ))
                # Git tracking check (LocalFetcher only; directories exempt)
                if getattr(self.gh, "is_local", False):
                    full = getattr(self.gh, "project_root") / path  # type: ignore[operator]
                    if full.is_file() and not getattr(self.gh, "_is_git_tracked")(path):
                        self.result.add(Finding(
                            check_id="C1",
                            dimension="Deliverable Completeness",
                            severity="CRITICAL" if required else "WARNING",
                            title=f"{'❌' if required else '⚠️'} {Path(path).name} on disk but NOT git-tracked.",
                            detail=f"Run: git add {path} && git commit",
                            rule_ref="HR-08" if required else "",
                        ))
            elif required:
                self.result.add(Finding(
                    check_id="C1",
                    dimension="Deliverable Completeness",
                    severity="CRITICAL",
                    title=f"Missing required deliverable: {description}",
                    detail=f"Searched paths: {', '.join(candidates)}",
                    rule_ref="HR-08",
                ))
            else:
                self.result.add(Finding(
                    check_id="C1",
                    dimension="Deliverable Completeness",
                    severity="WARNING",
                    title=f"Missing recommended deliverable: {description}",
                    detail=f"Searched paths: {', '.join(candidates)}",
                ))

        # -- C2: STAGE_PASS Structure Analysis ----------------------------────
    def check_c2_stage_pass(self):
        """C2: STAGE_PASS certificate completeness and quality"""
        sp_path = self._find_stage_pass_path()
        if sp_path is None:
            return

        content = self.gh.get_file_content(sp_path)
        if not content:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="CRITICAL", title="STAGE_PASS document unreadable",
                detail=sp_path,
            ))
            return

        self.result.add(Finding(
            check_id="C2", dimension="STAGE_PASS Certificate",
            severity="PASS", title="STAGE_PASS document exists",
            detail=sp_path,
        ))

        self._check_required_sections(content)
        self._check_quality_status(content)

    def _find_stage_pass_path(self) -> Optional[str]:
        """Locate the STAGE_PASS file in the git tree for this phase."""
        phase_patterns = [
            f"Phase{self.phase}_",
            f"Phase_{self.phase}_",
            f"Phase_{self.phase}-",
        ]
        tree_paths = [
            item["path"] for item in self.gh.get_tree()
            if any(pat in item["path"] for pat in phase_patterns)
            and "STAGE_PASS" in item["path"]
        ]
        tree_paths = sorted(tree_paths, key=lambda p: -len(p))
        if not tree_paths:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="CRITICAL",
                title=f"Phase{self.phase}_STAGE_PASS.md not found",
                detail="STAGE_PASS is a mandatory artifact since v6.06+; absence means audit flow was skipped",
                rule_ref="HR-08",
            ))
            return None
        return tree_paths[0]

    def _check_required_sections(self, content: str) -> None:
        """Check that all required sections are present in STAGE_PASS."""
        missing_sections = [
            s for s in STAGE_PASS_REQUIRED_SECTIONS if s not in content
        ]
        if missing_sections:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="WARNING",
                title=f"STAGE_PASS missing {len(missing_sections)} required section(s)",
                detail=f"Missing: {', '.join(missing_sections)}",
                rule_ref="HR-08",
            ))
        else:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="PASS",
                title="STAGE_PASS section structure complete",
                detail=f"Contains all required sections: {', '.join(STAGE_PASS_REQUIRED_SECTIONS)}",
            ))

    def _check_quality_status(self, content: str) -> None:
        """C2 supplement: verify machine STAGE_PASS contains quality_complete marker."""
        if "quality_complete: True" in content or "quality_complete=True" in content:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="PASS",
                title="STAGE_PASS contains quality_complete=True.",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="WARNING",
                title="STAGE_PASS missing quality_complete=True marker.",
                detail="Regenerate via finalize-gate to include quality status.",
            ))

    def _check_agent_b_review(self, content: str) -> None:
        """Verify Agent B review record exists in STAGE_PASS."""
        ab_found = any(kw in content for kw in STAGE_PASS_AGENT_B_KEYWORDS)
        if ab_found:
            found = [kw for kw in STAGE_PASS_AGENT_B_KEYWORDS if kw in content]
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="PASS", title="STAGE_PASS contains Agent B review record",
                detail=f"Found keywords: {found}",
            ))
        else:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="CRITICAL",
                title="STAGE_PASS missing Agent B review record",
                detail="Could not find APPROVE / reviewer / verdict keywords",
                rule_ref="HR-01",
            ))

    def _parse_and_check_confidence(self, content: str) -> None:
        """Parse confidence score from STAGE_PASS and add finding."""
        score_match = re.search(
            r"[*_]*Confidence Score[*_]*[:\uff1a]+\s*(\d+)/100", content
        )
        if not score_match:
            score_match = re.search(r"(\d{2,3})/100", content)
        if score_match:
            score = int(score_match.group(1))
            if score >= 70:
                sev, icon = "PASS", "\u2705"
            elif score >= 50:
                sev, icon = "WARNING", "\u26a0\ufe0f"
            else:
                sev, icon = "CRITICAL", "\u274c"
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity=sev,
                title=f"{icon} STAGE_PASS Confidence Score: {score}/100",
                detail="Threshold: >=90 (HR-11)", rule_ref="HR-11",
            ))
        else:
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="WARNING",
                title="Cannot parse confidence score from STAGE_PASS",
                detail="Could not find score in XX/100 format",
            ))

    def _check_johnny_confirm(self, content: str) -> None:
        """Check for Johnny HITL confirmation in STAGE_PASS."""
        if "Johnny" not in content:
            return
        if re.search(r"Johnny.*?(?:CONFIRM|\u2705|confirmed)", content, re.IGNORECASE):
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="PASS", title="Johnny HITL confirmation record exists",
                detail="Found Johnny CONFIRM record",
            ))
        elif re.search(r"Johnny.*?(?:\u23f3|pending|awaiting)", content, re.IGNORECASE):
            self.result.add(Finding(
                check_id="C2", dimension="STAGE_PASS Certificate",
                severity="WARNING",
                title="Johnny HITL not yet confirmed (pending)",
                detail="Johnny field in STAGE_PASS shows pending confirmation",
                rule_ref="HR-11",
            ))

    # -- C3: A/B Session Separation Verification ---------------------────
    def check_c3_session_separation(self):
        """C3: .methodology/sessions_spawn.log A/B different session verification"""
        content = self._content([".methodology/sessions_spawn.log", "sessions_spawn.log"])
        if not content:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="CRITICAL",
                title="sessions_spawn.log does not exist (checked .methodology/ and repo root)",
                detail="HR-10 mandates this file; absence means A/B collaboration cannot be verified",
                rule_ref="HR-10",
            ))
            return

        sessions = self._parse_session_records(content)
        if sessions is None:
            return

        self.result.add(Finding(
            check_id="C3", dimension="A/B Session Separation",
            severity="PASS",
            title=f"sessions_spawn.log exists — {len(sessions)} records found",
            detail="",
        ))

        roles, session_ids = self._extract_roles_and_ids(sessions)
        expected_a = self.spec.get("agent_a", "")
        expected_b = self.spec.get("agent_b", "")

        self._check_session_roles(roles, expected_a, expected_b)
        self._check_session_id_uniqueness(session_ids)
        self._check_empty_tasks(sessions)
        self._check_agent_b_approvals()

    def _parse_session_records(self, content: str) -> Optional[list]:
        """Parse line-delimited JSON from .methodology/sessions_spawn.log (or sessions_spawn.log fallback)."""
        sessions = []
        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sessions.append(json.loads(line))
            except json.JSONDecodeError:
                if "session_id" in line:
                    sessions.append({"raw": line})

        if not sessions:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="CRITICAL",
                title="sessions_spawn.log (in .methodology/ or root) is empty or unparseable",
                detail=f"First 100 chars: {content[:100]}",
                rule_ref="HR-10",
            ))
            return None
        return sessions

    def _extract_roles_and_ids(
        self, sessions: list
    ) -> tuple[set[str], set[str]]:
        """Extract unique roles and session_ids from parsed sessions."""
        roles = set()
        session_ids = set()
        for s in sessions:
            if isinstance(s, dict):
                role = s.get("role", "")
                sid = s.get("session_id", "")
                if role:
                    roles.add(role.lower())
                if sid:
                    session_ids.add(sid)
        return roles, session_ids

    def _check_session_roles(
        self, roles: set[str], expected_a: str, expected_b: str
    ) -> None:
        """Verify that both Agent A and Agent B roles are present."""
        has_a = expected_a in roles
        has_b = expected_b in roles
        if has_a and has_b:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="PASS",
                title=f"Found Agent A ({expected_a}) and Agent B ({expected_b}) records",
                detail=f"roles set: {roles}",
            ))
        else:
            missing = []
            if not has_a:
                missing.append(f"Agent A ({expected_a})")
            if not has_b:
                missing.append(f"Agent B ({expected_b})")
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="CRITICAL",
                title=f"sessions_spawn.log (in .methodology/ or root) missing roles: {', '.join(missing)}",
                detail=f"Found roles: {roles}; expected: {expected_a}, {expected_b}",
                rule_ref="HR-01",
            ))

    def _check_session_id_uniqueness(self, session_ids: set[str]) -> None:
        """Verify unique session IDs (A/B separation evidence)."""
        if len(session_ids) >= 2:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="PASS",
                title=f"Session IDs: {len(session_ids)} unique (A/B separation confirmed)",
                detail=f"IDs (first 20 chars): {[str(sid)[:20] for sid in list(session_ids)[:4]]}",
            ))
        elif len(session_ids) == 1:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="CRITICAL",
                title="All session_ids identical (suspected self-review)",
                detail=f"Unique session: {list(session_ids)[0]}",
                rule_ref="HR-01",
            ))
        else:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="WARNING",
                title="Cannot parse session_id values",
                detail=f"session_ids (first 4): {[str(sid)[:20] for sid in list(session_ids)[:4]]}",
            ))

    def _check_empty_tasks(self, sessions: list) -> None:
        """Check for empty task fields (OpenClaw system limitation)."""
        empty_tasks = sum(
            1 for s in sessions
            if isinstance(s, dict) and not s.get("task", "").strip()
        )
        if empty_tasks > 0:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="INFO",
                title=f"{empty_tasks} session record(s) have empty task field (OpenClaw system limitation)",
                detail="sessions_spawn.log (in .methodology/ or root) is generated by OpenClaw; Framework cannot control its format",
            ))

    def _check_agent_b_approvals(self) -> None:
        """C3 supplement: verify agent_b_approvals/*.json have review_status=APPROVE (P3+)."""
        if self.phase < 3:
            return  # P1/P2: sessions_spawn.log check is sufficient

        tree = self.gh.get_tree()
        approval_files = [
            item["path"] for item in tree
            if item["path"].startswith(".methodology/agent_b_approvals/")
            and item["path"].endswith(".json")
        ]
        if not approval_files:
            self.result.add(Finding(
                check_id="C3", dimension="A/B Session Separation",
                severity="CRITICAL",
                title="No Agent B approval files found in .methodology/agent_b_approvals/",
                detail="Each FR requires an approval JSON with review_status=APPROVE (P3+).",
                rule_ref="HR-01",
            ))
            return

        approved = 0
        for path in approval_files:
            c = self.gh.get_file_content(path)
            try:
                if c and json.loads(c).get("review_status") == "APPROVE":
                    approved += 1
            except json.JSONDecodeError:
                pass

        total = len(approval_files)
        if approved == total:
            sev = "PASS"
            icon = "✅"
            rule_ref = ""
        elif approved > 0:
            sev = "WARNING"
            icon = "⚠️"
            rule_ref = "HR-01"
        else:
            sev = "CRITICAL"
            icon = "❌"
            rule_ref = "HR-01"
        self.result.add(Finding(
            check_id="C3", dimension="A/B Session Separation",
            severity=sev,
            title=f"{icon} {approved}/{total} Agent B approval file(s) have review_status=APPROVE.",
            detail=f"Files checked: {[p.split('/')[-1] for p in approval_files[:5]]}",
            rule_ref=rule_ref,
        ))

    # -- C4: DEVELOPMENT_LOG Quality ---------------------------------────
    def check_c4_development_log(self):
        """C4: Check DEVELOPMENT_LOG for actual command output (not vague records)"""
        content = self._content(["DEVELOPMENT_LOG.md"])
        if not content:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="CRITICAL",
                title="DEVELOPMENT_LOG.md does not exist",
                detail="",
                rule_ref="HR-07",
            ))
            return

        # extract Phase-related content
        phase_pattern = re.compile(
            rf"##\s*Phase\s*{self.phase}[:\s]", re.IGNORECASE
        )
        has_phase_section = bool(phase_pattern.search(content))
        if has_phase_section:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="PASS",
                title=f"DEVELOPMENT_LOG contains Phase {self.phase} section",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="WARNING",
                title=f"DEVELOPMENT_LOG: no dedicated Phase {self.phase} section found",
                detail="May be mixed with other Phases, or section heading format does not match",
            ))

        # session_id records
        sid_match = re.search(r"session[_-]?id[:]\s*(\S+)", content, re.IGNORECASE)
        if sid_match:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="PASS",
                title="DEVELOPMENT_LOG records session_id",
                detail=f"Found: {sid_match.group(0)[:60]}",
                rule_ref="HR-07",
            ))
        else:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="WARNING",
                title="DEVELOPMENT_LOG: no session_id record found",
                detail="HR-07 requires this; absence deducts Integrity -15",
                rule_ref="HR-07",
            ))

        # QG actual output evidence
        qg_evidence_count = sum(
            1 for pat in QG_EVIDENCE_PATTERNS
            if re.search(pat, content, re.IGNORECASE)
        )
        if qg_evidence_count >= 2:
            matched = [
                pat for pat in QG_EVIDENCE_PATTERNS
                if re.search(pat, content, re.IGNORECASE)
            ]
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="PASS",
                title=f"DEVELOPMENT_LOG contains QG actual output evidence ({qg_evidence_count}/{len(QG_EVIDENCE_PATTERNS)} patterns)",
                detail=f"Matched patterns: {matched[:3]}",
                rule_ref="HR-02",
            ))
        elif qg_evidence_count == 1:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="WARNING",
                title=f"DEVELOPMENT_LOG: insufficient QG output evidence (only {qg_evidence_count} pattern(s))",
                detail="Expected to see Constitution scores, ASPICE results, and other tool outputs",
                rule_ref="HR-02",
            ))
        else:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="CRITICAL",
                title="DEVELOPMENT_LOG: no recognizable QG tool output",
                detail="No Constitution/ASPICE/pytest command output patterns found; suspected vague record",
                rule_ref="HR-02",
            ))

        # fake-pass detection
        lines = content.splitlines()
        fake_lines = []
        for i, line in enumerate(lines, 1):
            for pat in FAKE_PASS_PATTERNS:
                if re.match(pat, line.strip()):
                    fake_lines.append(f"Line {i}: {line.strip()}")
        if fake_lines:
            self.result.add(Finding(
                check_id="C4",
                dimension="DEVELOPMENT_LOG Quality",
                severity="WARNING",
                title=f"Detected {len(fake_lines)} line(s) of suspected vague pass markers",
                detail="\n".join(fake_lines[:3]),
                evidence="SKILL.md forbids writing only '\u2705 Passed' without actual command output",
            ))

    # -- C5: Phase Core Document Content Depth -----------------------
    def check_c5_content_depth(self):
        """C5: Content quality of core documents (SRS FR count, section completeness, etc.)"""
        phase = self.phase

        if phase == 1:
            self._check_srs_depth()
            self._check_spec_tracking_depth()
            self._check_traceability_depth(phase)

        elif phase == 2:
            self._check_sad_depth()

        elif phase in [3, 4]:
            if phase == 3:
                self._check_tdd_log_depth()
            else:  # phase == 4
                self._check_test_plan_depth()
                self._check_test_results_depth()

        elif phase == 5:
            self._check_baseline_depth()

        elif phase == 6:
            self._check_quality_report_depth()
            self._check_release_notes_depth()
            self._check_final_sign_off_depth()

        elif phase == 7:
            self._check_risk_register_depth()
            self._check_risk_status_report_depth()
            self._check_risk_mitigation_plans_depth()

        elif phase == 8:
            self._check_config_records_depth()
            self._check_release_checklist_depth()

    def _check_srs_depth(self):
        content = self._content(["01-requirements/SRS.md"])
        if not content:
            return

        # FR count
        fr_matches = re.findall(r"FR-\d+", content)
        fr_count = len(set(fr_matches))
        min_fr = self.spec.get("min_fr_count", 3)
        if fr_count >= min_fr:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"SRS.md contains {fr_count} Functional Requirement(s) (FR)",
                detail=f"Minimum required: {min_fr}; found: {sorted(set(fr_matches))}",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="CRITICAL" if fr_count == 0 else "WARNING",
                title=f"{'❌' if fr_count==0 else '⚠️'} SRS.md only has {fr_count} FR(s) (minimum: {min_fr})",
                detail=f"Found: {sorted(set(fr_matches))}",
            ))

        # logic verification method
        logic_count = len(re.findall(r"Logic Verification Method", content))
        if logic_count >= max(1, fr_count // 2):
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"SRS.md contains {logic_count} Logic Verification Method(s)",
                detail="Each FR should have a corresponding Logic Verification Method",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title=f"SRS.md insufficient Logic Verification Methods ({logic_count} vs {fr_count} FRs)",
                detail="SKILL.md Phase 1 requires each FR to have a Logic Verification Method",
            ))

        # NFR existence
        nfr_matches = re.findall(r"NFR-\d+", content)
        if nfr_matches:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"SRS.md contains {len(set(nfr_matches))} Non-Functional Requirement(s) (NFR)",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title="SRS.md: no NFR requirements found",
                detail="Recommended to include performance, availability, maintainability NFRs",
            ))

    def _check_spec_tracking_depth(self):
        content = self._content([
            "01-requirements/SPEC_TRACKING.md",
        ])
        if not content:
            return
        required_cols = self.spec.get("spec_tracking_required_cols", [])
        missing = [col for col in required_cols if col not in content]
        if not missing:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title="SPEC_TRACKING.md contains required columns",
                detail=f"Columns: {required_cols}",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title=f"SPEC_TRACKING.md missing columns: {missing}",
                detail="",
            ))

    def _check_traceability_depth(self, phase: int):
        content = self._content([
            "01-requirements/TRACEABILITY_MATRIX.md",
        ])
        if not content:
            return

        # Column existence check
        required_cols = self.spec.get("traceability_required_cols", [])
        missing = [col for col in required_cols if col not in content]
        if not missing:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title="TRACEABILITY_MATRIX.md contains required columns",
                detail="FR -> Module mapping table exists",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title=f"TRACEABILITY_MATRIX.md missing columns: {missing}",
                detail="",
            ))

        # FR coverage: cross-check every FR-ID from SRS.md appears in the matrix
        srs = self._content(["01-requirements/SRS.md"]) or ""
        srs_frs = sorted(set(re.findall(r"FR-\d+", srs)))
        if srs_frs:
            missing_frs = [fr for fr in srs_frs if fr not in content]
            covered = len(srs_frs) - len(missing_frs)
            pct = covered / len(srs_frs) * 100
            if pct >= 100:
                self.result.add(Finding(
                    check_id="C5", dimension="Document Content Depth",
                    severity="PASS",
                    title=f"TRACEABILITY_MATRIX.md covers all {len(srs_frs)} FR(s) from SRS.",
                    detail="",
                ))
            elif pct >= 80:
                self.result.add(Finding(
                    check_id="C5", dimension="Document Content Depth",
                    severity="WARNING",
                    title=(f"TRACEABILITY_MATRIX.md covers {covered}/{len(srs_frs)} FR(s) "
                           f"({pct:.0f}%) — {len(missing_frs)} missing."),
                    detail=f"Missing: {missing_frs[:5]}",
                ))
            else:
                self.result.add(Finding(
                    check_id="C5", dimension="Document Content Depth",
                    severity="CRITICAL",
                    title=(f"TRACEABILITY_MATRIX.md covers only {covered}/{len(srs_frs)} FR(s) "
                           f"({pct:.0f}%)."),
                    detail=f"Missing: {missing_frs[:5]}",
                    rule_ref="HR-05",
                ))

        # TBD module entries: any unfilled Module column
        tbd_count = len(re.findall(r'\|\s*TBD\s*\|', content, re.IGNORECASE))
        if tbd_count > 0:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="WARNING",
                title=f"TRACEABILITY_MATRIX.md has {tbd_count} Module entry/entries still TBD.",
                detail="Fill in module assignments before Phase 2.",
            ))

    def _check_tdd_log_depth(self):
        """C5 P3: Verify DEVELOPMENT_LOG.md contains TDD evidence."""
        content = self._content(["DEVELOPMENT_LOG.md"])
        if not content:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="WARNING",
                title="DEVELOPMENT_LOG.md missing — TDD evidence unverifiable.",
                detail="",
            ))
            return

        tdd_patterns = [
            r"\bRED\b", r"\bGREEN\b", r"\bREFACTOR\b",
            r"test[_\s]commit", r"TR-\d+", r"- \[x\].*test",
            r"test.*pass", r"pytest.*pass",
        ]
        hits = [p for p in tdd_patterns if re.search(p, content, re.IGNORECASE)]
        if len(hits) >= 2:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="PASS",
                title=f"DEVELOPMENT_LOG.md contains TDD evidence ({len(hits)} pattern(s)).",
                detail=f"Matched: {hits[:3]}",
            ))
        elif len(hits) == 1:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="WARNING",
                title="DEVELOPMENT_LOG.md has minimal TDD evidence (1 pattern).",
                detail="Expected RED/GREEN cycle records or test commit references.",
            ))
        else:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="CRITICAL",
                title="DEVELOPMENT_LOG.md has no TDD evidence.",
                detail="HR-06: P3 requires TDD. Log RED→GREEN cycles and test commits.",
                rule_ref="HR-06",
            ))

    def _check_sad_depth(self):
        """C5 P2: SAD.md structural check + FR coverage cross-check."""
        content = self._content(["02-architecture/SAD.md"])
        if not content:
            return

        # Structural keywords
        required = ["Module", "Architecture", "FR-"]
        missing_kw = [kw for kw in required if kw not in content]
        if missing_kw:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="WARNING",
                title=f"SAD.md missing keywords: {missing_kw}",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="PASS",
                title="SAD.md contains core architecture design content.",
                detail=f"Found keywords: {required}",
            ))

        # FR coverage: extract FR IDs from quality_manifest or SRS, verify all appear in SAD
        fr_ids: list[str] = []
        manifest_content = self._content([".methodology/quality_manifest.json"])
        if manifest_content:
            try:
                fr_ids = json.loads(manifest_content).get("fr_ids", [])
            except (json.JSONDecodeError, ValueError):
                pass

        if not fr_ids:
            # Fallback: extract from SRS.md
            srs = self._content(["01-requirements/SRS.md"]) or ""
            fr_ids = sorted(set(re.findall(r"FR-\d+", srs)))

        if not fr_ids:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="INFO",
                title="SAD.md FR coverage: no FR IDs found in SRS/manifest to cross-check.",
                detail="",
            ))
            return

        covered = [fr for fr in fr_ids if fr in content]
        pct = len(covered) / len(fr_ids) * 100
        sev = "PASS" if pct >= 80 else ("WARNING" if pct >= 50 else "CRITICAL")
        self.result.add(Finding(
            check_id="C5", dimension="Document Content Depth",
            severity=sev,
            title=f"SAD.md covers {len(covered)}/{len(fr_ids)} FR IDs ({pct:.0f}%)",
            detail=(f"Missing: {[fr for fr in fr_ids if fr not in content][:5]}"
                    if sev != "PASS" else ""),
            rule_ref="" if sev == "PASS" else "HR-05",
        ))

    def _check_test_plan_depth(self):
        content = self._content(["04-testing/TEST_PLAN.md"])
        if not content:
            return
        tc_count = len(re.findall(r"TC-\d+", content))
        if tc_count >= 3:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"TEST_PLAN.md contains {tc_count} test case(s) (TC)",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING" if tc_count > 0 else "CRITICAL",
                title=f"{'⚠️' if tc_count>0 else '❌'} TEST_PLAN.md only has {tc_count} TC(s) (minimum: 3)",
                detail="",
            ))

    def _check_test_results_depth(self):
        """C5 P4: Verify TEST_RESULTS.md contains pass rate and TC/TR references."""
        content = self._content(["04-testing/TEST_RESULTS.md"])
        if not content:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="CRITICAL",
                title="TEST_RESULTS.md missing — cannot verify test execution.",
                detail="",
                rule_ref="HR-10",
            ))
            return

        # Pass rate: "X passed" or "pass rate: N%"
        has_rate = bool(
            re.search(r"\d+\s*(?:passed|pass(?:ed)?)", content, re.IGNORECASE)
            or re.search(r"pass\s*rate[:\s]+\d+", content, re.IGNORECASE)
        )
        tc_refs = len(re.findall(r"TC-\d+", content))
        tr_refs = len(re.findall(r"TR-\d+", content))

        if has_rate and (tc_refs + tr_refs) >= 3:
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="PASS",
                title=(f"TEST_RESULTS.md contains pass rate data and "
                       f"{tc_refs} TC/{tr_refs} TR reference(s)."),
                detail="",
            ))
        else:
            missing = []
            if not has_rate:
                missing.append("pass rate")
            if tc_refs + tr_refs < 3:
                missing.append(f"TC/TR references (found {tc_refs}+{tr_refs})")
            self.result.add(Finding(
                check_id="C5", dimension="Document Content Depth",
                severity="WARNING",
                title=f"TEST_RESULTS.md insufficient: missing {', '.join(missing)}.",
                detail="",
            ))

    def _check_baseline_depth(self):
        content = self._content(["05-verification/BASELINE.md"])
        if not content:
            return
        h2_count = len(re.findall(r"^## ", content, re.MULTILINE))
        if h2_count >= 7:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"BASELINE.md has {h2_count} section(s) (>=7)",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="CRITICAL" if h2_count < 4 else "WARNING",
                title=f"{'❌' if h2_count<4 else '⚠️'} BASELINE.md only has {h2_count} section(s) (need 7)",
                detail="SKILL.md §Phase 5 requires 7 sections: Overview, Functional Baseline, Quality Baseline, Performance Baseline, Issue Log, Change Log, Acceptance Sign-off",
            ))

    def _check_quality_report_depth(self):
        content = self._content([
            "06-quality/QUALITY_REPORT.md"
        ])
        if not content:
            return
        h2_count = len(re.findall(r"^## ", content, re.MULTILINE))
        if h2_count >= 7:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"QUALITY_REPORT.md has {h2_count} section(s) (>=7)",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title=f"QUALITY_REPORT.md only has {h2_count} section(s) (need 7)",
                detail="",
            ))

    def _check_risk_register_depth(self):
        content = self._content([
            "07-risk/RISK_REGISTER.md"
        ])
        if not content:
            return
        risk_count = len(re.findall(r"(?:HIGH|MEDIUM|LOW|🔴|🟡|🟢)", content))
        if risk_count >= 3:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"RISK_REGISTER.md contains {risk_count} risk rating record(s)",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title=f"RISK_REGISTER.md has too few risk records ({risk_count})",
                detail="SKILL.md §Phase 7 requires 5-dimension risk identification, at least 1 per dimension",
            ))

    def _check_config_records_depth(self):
        content = self._content([
            "08-config/CONFIG_RECORDS.md"
        ])
        if not content:
            return
        h2_count = len(re.findall(r"^## ", content, re.MULTILINE))
        if h2_count >= 8:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="PASS",
                title=f"CONFIG_RECORDS.md has {h2_count} section(s) (>=8)",
                detail="",
            ))
        else:
            self.result.add(Finding(
                check_id="C5",
                dimension="Document Content Depth",
                severity="WARNING",
                title=f"CONFIG_RECORDS.md only has {h2_count} section(s) (need 8)",
                detail="",
            ))

    def _check_release_notes_depth(self):
        content = self._content(["RELEASE_NOTES.md"])
        if not content:
            return
        keywords = ["version", "release", "change", "fix", "feature"]
        found = [kw for kw in keywords
                 if re.search(rf"\b{re.escape(kw)}\b", content, re.IGNORECASE)]
        sev = "PASS" if len(found) >= 2 else "WARNING"
        self.result.add(Finding(
            check_id="C5", dimension="Document Content Depth", severity=sev,
            title=f"RELEASE_NOTES.md: {len(found)}/{len(keywords)} content keywords found",
            detail=f"Found: {found}",
        ))

    def _check_final_sign_off_depth(self):
        content = self._content(["FINAL_SIGN_OFF.md"])
        if not content:
            return
        keywords = ["APPROVE", "sign", "gate", "pass", "confirm"]
        found = [kw for kw in keywords
                 if re.search(rf"\b{re.escape(kw)}\b", content, re.IGNORECASE)]
        sev = "PASS" if len(found) >= 2 else "WARNING"
        self.result.add(Finding(
            check_id="C5", dimension="Document Content Depth", severity=sev,
            title=f"FINAL_SIGN_OFF.md: {len(found)}/{len(keywords)} approval keywords found",
            detail=f"Found: {found}",
        ))

    def _check_risk_status_report_depth(self):
        content = self._content(["07-risk/RISK_STATUS_REPORT.md", "07-risk/RISK_ASSESSMENT.md"])
        if not content:
            return
        h2_count = len(re.findall(r"^## ", content, re.MULTILINE))
        risk_keywords = ["HIGH", "MEDIUM", "LOW", "status", "mitigation"]
        found_kw = [kw for kw in risk_keywords
                    if re.search(rf"\b{re.escape(kw)}\b", content, re.IGNORECASE)]
        sev = "PASS" if h2_count >= 3 and len(found_kw) >= 2 else "WARNING"
        self.result.add(Finding(
            check_id="C5", dimension="Document Content Depth", severity=sev,
            title=f"RISK_STATUS_REPORT.md: {h2_count} sections, {len(found_kw)} risk keywords",
            detail=f"Keywords: {found_kw}",
        ))

    def _check_risk_mitigation_plans_depth(self):
        content = self._content(["07-risk/RISK_MITIGATION_PLANS.md"])
        if not content:
            return
        action_count = len(re.findall(
            r"(?:action|mitigation|owner|due|deadline)", content, re.IGNORECASE))
        sev = "PASS" if action_count >= 3 else "WARNING"
        self.result.add(Finding(
            check_id="C5", dimension="Document Content Depth", severity=sev,
            title=f"RISK_MITIGATION_PLANS.md: {action_count} action/owner/deadline reference(s)",
            detail="Expected: mitigation actions, owners, due dates per risk",
        ))

    def _check_release_checklist_depth(self):
        content = self._content(["08-config/RELEASE_CHECKLIST.md"])
        if not content:
            return
        checked = len(re.findall(r"^- \[x\]", content, re.MULTILINE | re.IGNORECASE))
        unchecked = len(re.findall(r"^- \[ \]", content, re.MULTILINE))
        total = checked + unchecked
        sev = "PASS" if total >= 5 and unchecked == 0 else "WARNING"
        self.result.add(Finding(
            check_id="C5", dimension="Document Content Depth", severity=sev,
            title=f"RELEASE_CHECKLIST.md: {checked}/{total} items checked",
            detail=f"{unchecked} item(s) still unchecked." if unchecked else "All items complete.",
        ))

    # -- C6: Commit Timeline Analysis --------------------------------────
    def check_c6_commit_timeline(self):
        """C6: GitHub commit timeline reasonableness"""
        commits = self.gh.get_commits(per_page=30)
        if not commits:
            self.result.add(Finding(
                check_id="C6",
                dimension="Commit Timeline",
                severity="WARNING",
                title="Cannot retrieve commit records",
                detail="",
            ))
            return

        # Phase-related commits
        phase_keywords = [
            f"phase {self.phase}", f"phase{self.phase}",
            f"Phase {self.phase}", f"Phase{self.phase}",
            f"Phase_{self.phase}", "STAGE_PASS",
        ]
        phase_commits = [
            c for c in commits
            if any(kw.lower() in c.get("commit", {}).get("message", "").lower()
                   for kw in phase_keywords)
        ]

        self.result.add(Finding(
            check_id="C6",
            dimension="Commit Timeline",
            severity="INFO",
            title=f"Found {len(phase_commits)} Phase {self.phase}-related commit(s)",
            detail="\n".join([
                f"  {c['sha'][:7]} {c['commit']['author']['date'][:16]} "
                f"| {c['commit']['message'][:60]}"
                for c in phase_commits[:5]
            ]),
        ))

        if len(phase_commits) >= 2:
            # calculate time span between earliest and latest commit
            times = []
            for c in phase_commits:
                ts = c.get("commit", {}).get("author", {}).get("date", "")
                if ts:
                    try:
                        times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                    except ValueError:
                        pass
            if len(times) >= 2:
                times.sort()
                duration_min = (times[-1] - times[0]).total_seconds() / 60
                min_required = self.spec.get("min_duration_minutes", 5)
                if duration_min >= min_required:
                    self.result.add(Finding(
                        check_id="C6",
                        dimension="Commit Timeline",
                        severity="PASS",
                        title=f"Phase {self.phase} commit span: {duration_min:.0f} min (minimum: {min_required} min)",
                        detail=f"First commit: {times[0].strftime('%H:%M')} -> Last commit: {times[-1].strftime('%H:%M')}",
                    ))
                else:
                    self.result.add(Finding(
                        check_id="C6",
                        dimension="Commit Timeline",
                        severity="WARNING",
                        title=f"Phase {self.phase} commit span only {duration_min:.0f} min (minimum: {min_required} min)",
                        detail="Execution time too short; may not have completed all steps",
                    ))

        # duplicate commit check (multiple fixes indicate iterative repair, which is normal)
        fix_commits = [
            c for c in phase_commits
            if "fix" in c.get("commit", {}).get("message", "").lower()
        ]
        if fix_commits:
            self.result.add(Finding(
                check_id="C6",
                dimension="Commit Timeline",
                severity="INFO",
                title=f"{len(fix_commits)} fix commit(s) found (shows iterative process, normal)",
                detail="\n".join([
                    f"  {c['sha'][:7]}: {c['commit']['message'][:60]}"
                    for c in fix_commits[:3]
                ]),
            ))

    # -- C7: Claims Cross-Verification --------------------------------────
    def check_c7_claims_crosscheck(self):
        """C7: Cross-verify FR coverage in DEVELOPMENT_LOG vs quality_manifest.json fr_ids.

        Replaces STAGE_PASS-dependent cross-check (v2.5.0+).
        Reports % of declared fr_ids found in DEVELOPMENT_LOG.md.
        """
        manifest_content = self._content([".methodology/quality_manifest.json"])
        if not manifest_content:
            self.result.add(Finding(
                check_id="C7", dimension="Claims Cross-Verification",
                severity="WARNING",
                title="quality_manifest.json missing — FR coverage unverifiable.",
                detail="",
            ))
            return

        try:
            manifest = json.loads(manifest_content)
        except json.JSONDecodeError:
            self.result.add(Finding(
                check_id="C7", dimension="Claims Cross-Verification",
                severity="WARNING",
                title="quality_manifest.json not parseable.",
                detail="",
            ))
            return

        fr_ids: list[str] = manifest.get("fr_ids", [])
        if not fr_ids:
            self.result.add(Finding(
                check_id="C7", dimension="Claims Cross-Verification",
                severity="INFO",
                title="quality_manifest.json has no fr_ids — FR coverage check skipped.",
                detail="",
            ))
            return

        devlog = self._content(["DEVELOPMENT_LOG.md"]) or ""
        found = [fr for fr in fr_ids if fr in devlog]
        coverage = len(found) / len(fr_ids) * 100

        sev = "PASS" if coverage >= 80.0 else "WARNING"
        self.result.add(Finding(
            check_id="C7", dimension="Claims Cross-Verification",
            severity=sev,
            title=(f"FR coverage in DEVELOPMENT_LOG: "
                   f"{len(found)}/{len(fr_ids)} ({coverage:.0f}%)"),
            detail=(f"Missing FRs: {[fr for fr in fr_ids if fr not in devlog][:5]}"
                    if sev == "WARNING" else ""),
            rule_ref="" if sev == "PASS" else "HR-07",
        ))

    # -- C8: Integrity Tracker Status ---------------------------------──
    def check_c8_integrity(self):
        """C8: .integrity_tracker.json integrity score (if exists)"""
        content = self._content([".integrity_tracker.json"])
        if not content:
            self.result.add(Finding(
                check_id="C8",
                dimension="Integrity Tracker",
                severity="INFO",
                title=".integrity_tracker.json does not exist on GitHub",
                detail="May be a local tool, not uploaded to GitHub (acceptable)",
            ))
            return

        try:
            data = json.loads(content)
            score = data.get("integrity_score", 100)
            violations = data.get("violations", [])

            if score >= 80:
                sev = "PASS"
                icon = "✅"
            elif score >= 50:
                sev = "WARNING"
                icon = "⚠️"
            else:
                sev = "CRITICAL"
                icon = "❌"

            self.result.add(Finding(
                check_id="C8",
                dimension="Integrity Tracker",
                severity=sev,
                title=f"{icon} Integrity Score: {score}/100 ({['LOW_TRUST','PARTIAL_TRUST','FULL_TRUST'][0 if score<50 else 1 if score<80 else 2]})",
                detail=f"Violation records: {len(violations)}",
                rule_ref="HR-09",
            ))

            if violations:
                self.result.add(Finding(
                    check_id="C8",
                    dimension="Integrity Tracker",
                    severity="WARNING",
                    title="Integrity violation records:",
                    detail="\n".join([
                        f"  - {v.get('type','?')}: {v.get('details','')[:60]}"
                        for v in violations[:5]
                    ]),
                ))
        except json.JSONDecodeError:
            self.result.add(Finding(
                check_id="C8",
                dimension="Integrity Tracker",
                severity="WARNING",
                title=".integrity_tracker.json format cannot be parsed",
                detail=content[:100],
            ))

    # -- C9: quality_manifest.json Gate PASS Verification -----------────
    def check_c9_gate_pass(self):
        """C9: Verify quality_manifest.json records required gate PASS for current phase."""
        gate_num = _ENTRY_GATE_MAP.get(self.phase)
        if gate_num is None:
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="INFO",
                title=f"Phase {self.phase} has no required gate record (P1–P3).",
                detail="Gate entry requirements start from Phase 4.",
            ))
            return

        content = self._content([".methodology/quality_manifest.json"])
        if not content:
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="CRITICAL",
                title=f"quality_manifest.json missing — Gate {gate_num} PASS cannot be verified.",
                detail="Required from Phase 4+ (generated at P2 exit by plan-phase).",
                rule_ref="HR-08",
            ))
            return

        try:
            manifest = json.loads(content)
        except json.JSONDecodeError as exc:
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="CRITICAL",
                title=f"quality_manifest.json is not valid JSON: {exc}",
                detail="",
            ))
            return

        gate_result = manifest.get("gate_results", {}).get(f"gate{gate_num}", {})
        if not gate_result:
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="CRITICAL",
                title=f"Gate {gate_num} result missing in quality_manifest.json for Phase {self.phase}.",
                detail=f"Expected: gate_results.gate{gate_num}.quality_complete = true",
                rule_ref="HR-08",
            ))
            return

        if gate_result.get("quality_complete"):
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="PASS",
                title=f"Gate {gate_num} PASS confirmed in quality_manifest.json.",
                detail=f"quality_complete=True for Phase {self.phase} entry requirement.",
            ))
        else:
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="CRITICAL",
                title=f"Gate {gate_num} NOT passed — quality_complete != True.",
                detail=f"Got: {gate_result!r}",
                rule_ref="HR-08",
            ))

        # Numeric score threshold check
        score_val = gate_result.get("score")
        threshold = _GATE_SCORE_THRESHOLDS.get(gate_num)
        if threshold is not None and score_val is not None:
            try:
                score_f = float(score_val)
            except (TypeError, ValueError):
                score_f = None
            if score_f is None:
                self.result.add(Finding(
                    check_id="C9", dimension="Gate PASS Record",
                    severity="WARNING",
                    title=f"Gate {gate_num} score unreadable (value={score_val!r}).",
                    detail="Expected numeric value in quality_manifest.json gate_results.",
                ))
            elif score_f < threshold:
                self.result.add(Finding(
                    check_id="C9", dimension="Gate PASS Record",
                    severity="CRITICAL",
                    title=f"Gate {gate_num} score {score_f:.1f}% below threshold {threshold:.0f}%.",
                    detail=f"quality_manifest.json gate_results.gate{gate_num}.score = {score_f}",
                    rule_ref="TH-02" if gate_num >= 3 else "TH-11",
                ))
            else:
                self.result.add(Finding(
                    check_id="C9", dimension="Gate PASS Record",
                    severity="PASS",
                    title=f"Gate {gate_num} score {score_f:.1f}% ≥ threshold {threshold:.0f}%.",
                    detail="",
                ))
        elif threshold is not None and score_val is None:
            self.result.add(Finding(
                check_id="C9", dimension="Gate PASS Record",
                severity="WARNING",
                title=f"Gate {gate_num} score field absent from quality_manifest.json.",
                detail="Cannot verify numeric threshold compliance.",
            ))

    # -- C10: Local State Consistency (LocalFetcher only) -----------────
    def check_c10_local_state(self):
        """C10: Local-only checks — only runs when using LocalFetcher.

        Checks: (1) state.json current_phase matches audited phase,
                (2) gate4_result.json present for P6+ (Gate 4 entry).
        Silently no-ops when using GitHubFetcher (is_local = False).
        """
        if not getattr(self.gh, "is_local", False):
            return  # Skip in GitHub mode

        # 1. state.json phase consistency
        state_content = self.gh.get_file_content(".methodology/state.json")
        if state_content:
            try:
                state = json.loads(state_content)
                current = int(state.get("current_phase", 0))
                if current != self.phase:
                    self.result.add(Finding(
                        check_id="C10", dimension="Local State Consistency",
                        severity="WARNING",
                        title=f"state.json current_phase={current} ≠ audited phase {self.phase}",
                        detail="Audit may be running against wrong phase. Check advance-phase.",
                    ))
                else:
                    self.result.add(Finding(
                        check_id="C10", dimension="Local State Consistency",
                        severity="PASS",
                        title=f"state.json current_phase={current} matches audited phase.",
                        detail="",
                    ))
            except (json.JSONDecodeError, ValueError):
                self.result.add(Finding(
                    check_id="C10", dimension="Local State Consistency",
                    severity="WARNING",
                    title="state.json is not valid JSON or missing current_phase.",
                    detail="",
                ))

        # 2. gate4_result.json for P6+ (mirrors harness_cli._check_gate4_prerequisites paths)
        if self.phase >= 6:
            _g4_candidates = [
                ".sessi-work/gate4_result.json",     # primary (written by bridge)
                ".methodology/gate4_result.json",    # fallback
                "gate4_result.json",                 # root fallback
            ]
            g4_path = self.gh.resolve_path(_g4_candidates)
            g4 = self.gh.get_file_content(g4_path) if g4_path else None
            if not g4:
                self.result.add(Finding(
                    check_id="C10", dimension="Local State Consistency",
                    severity="CRITICAL",
                    title="gate4_result.json missing — Gate 4 PASS evidence absent (P6+).",
                    detail="Run finalize-gate --gate 4 --project . to generate it.",
                    rule_ref="HR-08",
                ))
            else:
                try:
                    data = json.loads(g4)
                    passed = data.get("quality_complete") or data.get("passed")
                    sev = "PASS" if passed else "CRITICAL"
                    self.result.add(Finding(
                        check_id="C10", dimension="Local State Consistency",
                        severity=sev,
                        title=f"gate4_result.json: quality_complete={passed} (found at {g4_path})",
                        detail=repr(data)[:120],
                    ))
                except json.JSONDecodeError:
                    self.result.add(Finding(
                        check_id="C10", dimension="Local State Consistency",
                        severity="WARNING",
                        title="gate4_result.json is not valid JSON.",
                        detail="",
                    ))

    # -- C11: Plan Checklist ------------------------------------------────
    def check_c11_plan_checklist(self):
        """C11: Verify phase plan has no unchecked mandatory items."""
        plan_content = self.gh.get_file_content(
            f".methodology/phase{self.phase}_plan.md"
        )
        if not plan_content:
            self.result.add(Finding(
                check_id="C11", dimension="Plan Checklist",
                severity="WARNING",
                title=f"phase{self.phase}_plan.md not found — checklist unverifiable.",
                detail="",
            ))
            return

        skip_re = re.compile(
            r"\[A-DISPATCH\]|\[B-DISPATCH\]|\[INFO\]|\[OPTIONAL\]|\[SKIP\]"
            r"|Gate \d+.*score|Phase \d+.*complete",
            re.IGNORECASE,
        )
        unchecked = [
            line.strip()[5:].strip()
            for line in plan_content.splitlines()
            if line.strip().startswith("- [ ]") and not skip_re.search(line)
        ]

        if unchecked:
            self.result.add(Finding(
                check_id="C11", dimension="Plan Checklist",
                severity="CRITICAL",
                title=f"{len(unchecked)} unchecked mandatory plan item(s).",
                detail=f"First: {unchecked[:3]}",
                rule_ref="HR-03",
            ))
        else:
            self.result.add(Finding(
                check_id="C11", dimension="Plan Checklist",
                severity="PASS",
                title="All mandatory plan items are checked.",
                detail=f"Phase {self.phase} plan checklist complete.",
            ))

    # -- C12: Git Milestones ------------------------------------------────
    def check_c12_git_milestones(self):
        """C12: Verify required milestone commits exist in git history."""
        milestones = _PHASE_MILESTONES.get(self.phase, [])
        if not milestones:
            self.result.add(Finding(
                check_id="C12", dimension="Git Milestones",
                severity="INFO",
                title=f"Phase {self.phase} has no mandatory milestone commits.",
                detail="",
            ))
            return

        commits = self.gh.get_commits(per_page=50)
        log_text = " ".join(
            c.get("commit", {}).get("message", "").lower()
            for c in commits
        )
        missing = [ms for ms in milestones if ms.lower() not in log_text]
        if missing:
            self.result.add(Finding(
                check_id="C12", dimension="Git Milestones",
                severity="WARNING",
                title=f"{len(missing)}/{len(milestones)} milestone commit(s) not found.",
                detail=f"Missing: {missing}",
                rule_ref="HR-03",
            ))
        else:
            self.result.add(Finding(
                check_id="C12", dimension="Git Milestones",
                severity="PASS",
                title=f"All {len(milestones)} milestone commit(s) found.",
                detail=f"Milestones: {milestones}",
            ))

    # -- Run all checks -------------------------------------------────
    def run_all_checks(self) -> AuditResult:
        """Execute all C1-C12 checks and compute final audit score."""
        print(f"\n{'='*60}")
        print(f"Auditing {self.gh.repo} -- Phase {self.phase}: {self.spec.get('name','')}")
        print(f"{'='*60}")

        checks = [
            ("C1  Deliverable Completeness",   self.check_c1_deliverables),
            ("C2  STAGE_PASS Certificate",     self.check_c2_stage_pass),
            ("C3  A/B Session Separation",     self.check_c3_session_separation),
            ("C4  DEVELOPMENT_LOG Quality",    self.check_c4_development_log),
            ("C5  Document Content Depth",     self.check_c5_content_depth),
            ("C6  Commit Timeline",            self.check_c6_commit_timeline),
            ("C7  Claims Cross-Verification",  self.check_c7_claims_crosscheck),
            ("C8  Integrity Tracker",          self.check_c8_integrity),
            ("C9  Gate PASS Record",           self.check_c9_gate_pass),
            ("C10 Local State Consistency",    self.check_c10_local_state),
            ("C11 Plan Checklist",             self.check_c11_plan_checklist),
            ("C12 Git Milestones",             self.check_c12_git_milestones),
        ]
        for name, fn in checks:
            print(f"  → {name}...", end=" ", flush=True)
            fn()
            print("done")

        self._calculate_score()
        return self.result

    def _calculate_score(self):
        """Calculate composite audit score and final verdict"""
        findings = self.result.findings
        criticals = len([f for f in findings if f.severity == "CRITICAL"])
        warnings  = len([f for f in findings if f.severity == "WARNING"])
        passes    = len([f for f in findings if f.severity == "PASS"])

        total = criticals + warnings + passes
        if total == 0:
            self.result.score = 0
            self.result.verdict = "FAIL"
            return

        # weighted score: PASS=+1pt, WARNING=-0.3pt, CRITICAL=-1.5pt (relative to pass baseline)
        raw = passes - (warnings * 0.3) - (criticals * 1.5)
        self.result.score = max(0, min(100, (raw / total) * 100))

        if criticals == 0 and self.result.score >= 60:
            self.result.verdict = "PASS"
        elif criticals <= 1 and self.result.score >= 40:
            self.result.verdict = "CONDITIONAL_PASS"
        else:
            self.result.verdict = "FAIL"


# ─────────────────────────────────────────────
# 5. REPORT GENERATOR
# ─────────────────────────────────────────────

def generate_report(result: AuditResult, output_format: str = "markdown") -> str:
    """Render audit results as markdown or JSON report."""
    verdict_icon = {"PASS": "\u2705", "CONDITIONAL_PASS": "\u26a0\ufe0f", "FAIL": "\u274c"}.get(result.verdict, "\u2753")
    verdict_label = {
        "PASS": "Passed",
        "CONDITIONAL_PASS": "Conditional Pass (fix required)",
        "FAIL": "Failed",
    }.get(result.verdict, result.verdict)

    findings_by_dim: dict[str, list[Finding]] = {}
    for f in result.findings:
        findings_by_dim.setdefault(f.dimension, []).append(f)

    criticals = result.criticals()
    warnings  = result.warnings()
    passes    = result.passes()

    lines: list[str] = []
    lines += _report_header(result, verdict_icon, verdict_label, criticals, warnings, passes)
    lines += _report_criticals(criticals)
    lines += _report_warnings(warnings)
    lines += _report_dimensions(findings_by_dim)
    lines += _report_recommendations(criticals, warnings)
    lines += _report_next_steps(result)
    lines += _report_footer()
    return "\n".join(lines)


def _report_header(
    result: AuditResult, verdict_icon: str, verdict_label: str,
    criticals: list, warnings: list, passes: list,
) -> list[str]:
    """Render audit report header with repo/phase/verdict metadata."""
    return [
        f"# Audit Report -- Phase {result.phase}: {result.phase_name}",
        "",
        f"> **Project**: {result.repo}  ",
        f"> **Audit Time**: {result.audit_time}  ",
        "> **Methodology Version**: harness-methodology v6.49  ",
        "> **Audit Tool**: phase_auditor.py  ",
        "",
        "---",
        "",
        "## Final Verdict",
        "",
        "| Item | Value |",
        "|------|------|",
        f"| Verdict | {verdict_icon} **{verdict_label}** |",
        f"| Audit Score | **{result.score:.1f} / 100** |",
        f"| Critical Issues (CRITICAL) | {len(criticals)} |",
        f"| Warnings (WARNING) | {len(warnings)} |",
        f"| Passed Items (PASS) | {len(passes)} |",
        "",
    ]


def _report_criticals(criticals: list) -> list[str]:
    """Format critical findings as markdown list items."""
    if not criticals:
        return []
    lines = [
        "## Critical Issues (must be fixed before entering next Phase)",
        "",
    ]
    for f in criticals:
        lines.append(f"### {f.title}")
        lines.append(f"- **Dimension**: {f.dimension}")
        lines.append(f"- **Check ID**: {f.check_id}")
        if f.rule_ref:
            lines.append(f"- **Rule Ref**: {f.rule_ref} -- {HARD_RULES.get(f.rule_ref, '')}")
        lines.append(f"- **Detail**: {f.detail}")
        if f.evidence:
            lines.append(f"- **Evidence**: {f.evidence}")
        lines.append("")
    return lines


def _report_warnings(warnings: list) -> list[str]:
    """Format warning findings as markdown list items."""
    if not warnings:
        return []
    lines = [
        "## Warnings (recommended fixes)",
        "",
    ]
    for f in warnings:
        lines.append(f"- {f.title}")
        if f.detail:
            lines.append(f"  - {f.detail}")
        if f.rule_ref:
            lines.append(f"  - Rule: {f.rule_ref}")
    lines.append("")
    return lines


def _report_dimensions(findings_by_dim: dict) -> list[str]:
    """Render per-dimension finding counts in markdown table."""
    lines = [
        "## Per-Dimension Detailed Results",
        "",
    ]
    for dim, dim_findings in findings_by_dim.items():
        dim_criticals = sum(1 for f in dim_findings if f.severity == "CRITICAL")
        dim_warnings  = sum(1 for f in dim_findings if f.severity == "WARNING")
        dim_icon = "\U0001f534" if dim_criticals > 0 else ("\U0001f7e1" if dim_warnings > 0 else "\u2705")
        lines.append(f"### {dim_icon} {dim}")
        lines.append("")
        for f in dim_findings:
            lines.append(f"- {f.title}")
            if f.detail and f.severity != "PASS":
                for detail_line in f.detail.splitlines():
                    lines.append(f"  > {detail_line}")
        lines.append("")
    return lines


def _report_recommendations(criticals: list, warnings: list) -> list[str]:
    """Generate actionable fix recommendations from findings."""
    if not criticals and not warnings:
        return []
    lines = [
        "## fix recommendations",
        "",
    ]
    for i, f in enumerate(criticals, 1):
        t = f.title.lstrip('\u274c ')
        lines.append(f"{i}. **[CRITICAL]** {t}")
        if f.detail:
            lines.append(f"   - {f.detail.splitlines()[0]}")
    offset = len(criticals) + 1
    for i, f in enumerate(warnings, offset):
        t = f.title.lstrip('\u26a0\ufe0f ')
        lines.append(f"{i}. **[WARNING]** {t}")
    lines.append("")
    return lines

def _report_next_steps(result: AuditResult) -> list[str]:
    """Generate next-step actions based on audit verdict."""
    lines = [
        "## next steps",
        "",
    ]
    if result.verdict == "PASS":
        lines.append(f"Phase {result.phase} audit passed; may proceed to Phase {result.phase + 1}.")
    elif result.verdict == "CONDITIONAL_PASS":
        lines.append(f"After fixing the above WARNING items, re-run `python phase_auditor.py --repo {result.repo} --phase {result.phase}` to re-verify.")
    else:
        lines.append(f"Fix all CRITICAL issues, resubmit Phase {result.phase} artifacts, then re-run the audit.")
    return lines


def _report_footer() -> list[str]:
    """Render audit report footer with generation metadata."""
    return [
        "",
        "---",
        "*Auto-generated by phase_auditor.py | harness-methodology v6.49*",
    ]


# ─────────────────────────────────────────────
# 6. MAIN ENTRY POINT
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="harness-methodology Phase Auditor -- independent audit tool based on GitHub artifacts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Required arguments (project_context)

  Required:
    --repo    GitHub repo in owner/repo format
              e.g.: OWNER/my-project
    --phase   Phase number to audit (1-8)

  Optional (have reasonable defaults):
    --branch  Target branch (default: main)
    --output  Output format: markdown|json (default: markdown)
    --save    Save report to specified file

  Auto-detected (no need to provide):
    - methodology version (detected from STAGE_PASS or DEVELOPMENT_LOG)
    - Phase spec (built-in SKILL.md v6.13 rule library)
    - Document paths (supports multiple naming conventions)

Examples:
    python phase_auditor.py --repo johnnylugm-tech/tts-kokoro-v613 --phase 1
    python phase_auditor.py --repo OWNER/REPO --phase 3 --output json
    python phase_auditor.py --repo OWNER/REPO --phase 1 --save audit_phase1.md
        """,
    )
    parser.add_argument("--repo", required=True,
                        help="GitHub repo (owner/repo)")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 9),
                        help="Phase number to audit (1-8)")
    parser.add_argument("--branch", default="main",
                        help="Target branch (default: main)")
    parser.add_argument("--output", choices=["markdown", "json"], default="markdown",
                        help="Output format (markdown|json)")
    parser.add_argument("--save", metavar="FILE",
                        help="Save report to file")
    args = parser.parse_args()

    if args.phase not in PHASE_SPEC:
        print(f"Phase {args.phase} not defined; supported range: 1-8", file=sys.stderr)
        sys.exit(1)

    # initialize GitHub access layer
    fetcher = GitHubFetcher(repo=args.repo, branch=args.branch)

    # verify repo is accessible
    repo_info = fetcher.get_repo_info()
    if not repo_info:
        print(f"Cannot access repo: {args.repo} (check gh auth status)", file=sys.stderr)
        sys.exit(1)

    # run audit
    auditor = PhaseAuditor(fetcher=fetcher, phase=args.phase)
    result = auditor.run_all_checks()

    # output report
    if args.output == "json":
        output = json.dumps({
            "repo": result.repo,
            "phase": result.phase,
            "phase_name": result.phase_name,
            "audit_time": result.audit_time,
            "score": result.score,
            "verdict": result.verdict,
            "findings": [
                {
                    "check_id": f.check_id,
                    "dimension": f.dimension,
                    "severity": f.severity,
                    "title": f.title,
                    "detail": f.detail,
                    "rule_ref": f.rule_ref,
                }
                for f in result.findings
            ],
        }, ensure_ascii=False, indent=2)
    else:
        output = generate_report(result)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fp:
            fp.write(output)
        print(f"\nReport saved to: {args.save}")
    else:
        print("\n" + output)

    # Exit code
    exit_codes = {"PASS": 0, "CONDITIONAL_PASS": 1, "FAIL": 2}
    sys.exit(exit_codes.get(result.verdict, 2))


if __name__ == "__main__":
    main()

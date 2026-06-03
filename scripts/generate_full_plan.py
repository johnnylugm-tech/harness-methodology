#!/usr/bin/env python3
"""
Generate Full Plan with Phase-Specific Detailed Tasks

This script parses previous phase artifacts to generate detailed tasks
for each phase in the harness-methodology framework.

Phase Artifacts Mapping:
- Phase 1: (no previous artifacts)
- Phase 2: SRS.md -> Architecture requirements
- Phase 3: SRS.md + SAD.md -> Implementation tasks
- Phase 4: SRS.md + SAD.md + Code -> Testing tasks
- Phase 5: TEST_RESULTS.md + BASELINE.md -> Verification tasks
- Phase 6: QUALITY_REPORT.md -> Quality assurance tasks
- Phase 7: RISK_REGISTER.md -> Risk management tasks
- Phase 8: CONFIG_RECORDS.md -> Configuration tasks

Usage:
    python3 scripts/generate_full_plan.py --phase 3 --repo /path/to/project
    python3 scripts/generate_full_plan.py --phase 3 --repo /path/to/project --output phase3_FULL.md
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, cast


def _get_harness_version() -> str:
    """Read harness version from pyproject.toml (stdlib only, no tomllib needed)."""
    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        content = pyproject.read_text()
        # Anchor to [project] table to avoid matching dependency version strings
        m = re.search(r'\[project\]\n.*?\nversion\s*=\s*"([^"]+)"', content, re.DOTALL)
        if not m:
            m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        return m.group(1) if m else "2.4.0"
    except Exception:
        return "2.4.0"


_HARNESS_VERSION = _get_harness_version()

# ============================================================================
# Phase-Specific Parsers
# ============================================================================

def parse_srs_fr_sections(srs_path) -> List[Dict]:
    """Parse SRS.md to extract FR sections"""
    if srs_path is None:
        return []
    srs_path = Path(srs_path)
    if not srs_path.exists():
        return []

    content = srs_path.read_text(encoding='utf-8')

    # Also read SAD.md for complete FR list
    repo_path = srs_path.parent.parent
    sad_path = repo_path / "02-architecture" / "SAD.md"
    if sad_path.exists():
        sad_content = sad_path.read_text(encoding='utf-8')
        content += "\n" + sad_content

    # Find all FR sections (FR-01 to FR-99)
    # Note: pattern uses full-width colon to match Chinese-formatted SRS
    fr_pattern = re.compile(r'(### FR-(\d+):[^\n]+\n\n)(.*?)(?=\n---\n|\n### FR-\d+|$)', re.DOTALL)

    frs = []
    for m in fr_pattern.finditer(content):
        fr_num = f"FR-{m.group(2).zfill(2)}"
        title = m.group(1).strip().split('\n')[0].replace('### ', '')
        details = m.group(3).strip()

        # Extract description (matches Chinese SRS format)
        desc_match = re.search(r'\*\*Description\*\*:(.+?)(?:\n|$)', details, re.DOTALL)
        desc = desc_match.group(1).strip() if desc_match else ""

        # Extract test cases (matches Chinese SRS format)
        test_cases = re.findall(r'[Tt]est [Cc]ases?:[^"]+"([^"]+)"[^"]+"([^"]+)"', details)

        # Extract key requirements
        req_lines = []
        if 'Content' in details:
            content_section = details.split('Content')[1].split('**')[0].strip()
            req_lines = [line.strip() for line in content_section.split('\n') if line.strip() and line.strip().startswith('-')]

        frs.append({
            'fr': fr_num,
            'title': title,
            'desc': desc,
            'test_cases': test_cases,
            'requirements': req_lines,
            'raw_details': details[:500]
        })

    # Fallback: if no section-format FRs found, try table-format extraction.
    # Many projects write SRS.md FRs as a markdown table (| FR-01 | desc | ... |)
    # rather than ### FR-01: section headers.  This fallback extracts at least the
    # FR IDs and descriptions so the plan generator can produce per-FR task blocks.
    if not frs:
        table_re = re.compile(
            r'^\|\s*FR-(\d+)\s*\|\s*(.+?)\s*\|',
            re.MULTILINE,
        )
        seen = set()
        for m in table_re.finditer(content):
            fr_num = f"FR-{m.group(1).zfill(2)}"
            if fr_num in seen:
                continue
            seen.add(fr_num)
            desc = m.group(2).strip()
            # Truncate overly long table-cell descriptions
            if len(desc) > 200:
                desc = desc[:197] + "..."
            frs.append({
                'fr': fr_num,
                'title': f"{fr_num}: {desc[:80]}",
                'desc': desc,
                'test_cases': [],
                'requirements': [],
                'raw_details': desc,
            })

    if not frs:
        print(
            "[generate_full_plan] WARNING: No FR sections found in SRS.md.\n"
            "  Expected format: '### FR-01: Title' sections or '| FR-01 | desc |' table rows.\n"
            "  The generated plan will have no per-FR task blocks. Verify SRS.md format.",
            file=sys.stderr,
        )

    return frs


def parse_sad_modules(repo_path: Path) -> Dict:
    """Parse SAD.md to get FR -> module mapping"""
    sad_paths = [
        repo_path / "02-architecture" / "SAD.md",
    ]

    for sad_path in sad_paths:
        if not sad_path.exists():
            continue

        content = sad_path.read_text(encoding='utf-8')

        # Method 1: inline pattern — FR-01 ... `app/models/schema.py`
        simple_pattern = re.compile(r'FR-(\d+)[^\n]*?`?(?:app/|03-development/src/)([^\s`]+)`?', re.DOTALL)
        modules = {}
        seen = set()

        for m in simple_pattern.finditer(content):
            fr_num = m.group(1)
            if fr_num in seen:
                continue
            file_path = m.group(2) or ""
            seen.add(fr_num)

            if '/' in file_path:
                filename = file_path.split('/')[-1].replace('.py', '')
                if not file_path.startswith('03-development'):
                    file_path = f"03-development/src/{file_path}"
                modules[f"FR-{fr_num}"] = {
                    'module': filename,
                    'file': file_path
                }

        if modules:
            return modules

        # Method 2: JSON SAB block — "FR-01": "app.models"
        sab_match = re.search(r'"FR-\d+":\s*"([^"]+)"', content)
        if sab_match:
            sab_pattern = re.compile(r'"(FR-\d+)":\s*"([^"]+)"')
            for m in sab_pattern.finditer(content):
                fr_key = m.group(1)
                if fr_key in seen:
                    continue
                seen.add(fr_key)
                module_path = m.group(2)
                # Handle "a.b + c.d" style multi-module entries — take the last one
                if ' + ' in module_path:
                    print(
                        f"[generate_full_plan] WARNING: FR {fr_key} maps to multiple modules ({module_path}).\n"
                        f"  Only the last module ({module_path.split(' + ')[-1]}) will be assigned to Agent A.",
                        file=sys.stderr,
                    )
                    module_path = module_path.split(' + ')[-1]
                modules[fr_key] = {
                    'module': module_path.split('.')[-1] if '.' in module_path else module_path,
                    'file': f"03-development/src/{module_path.replace('.', '/')}.py"
                        if '.' in module_path
                        else f"03-development/src/{module_path}.py",
                }

        if modules:
            return modules

    return {}


def parse_test_plan(repo_path: Path) -> List[Dict]:
    """Parse TEST_PLAN.md to extract test requirements"""
    test_plan_paths = [
        repo_path / "04-testing" / "TEST_PLAN.md",
    ]

    for tp_path in test_plan_paths:
        if not tp_path.exists():
            continue

        content = tp_path.read_text(encoding='utf-8')

        test_pattern = re.compile(r'(###\s+\d+\.\d+\s+[^\n]+\n)(.*?)(?=\n###|\n##|\Z)', re.DOTALL)
        tests = []

        for m in test_pattern.finditer(content):
            title = m.group(1).strip().replace('### ', '')
            details = m.group(2).strip()[:300]
            tests.append({
                'title': title,
                'details': details
            })

        if tests:
            return tests

    return []


def parse_quality_report(repo_path: Path) -> Dict:
    """Parse QUALITY_REPORT.md"""
    qr_paths = [
        repo_path / "06-quality" / "QUALITY_REPORT.md",
    ]

    for qr_path in qr_paths:
        if not qr_path.exists():
            continue

        content = qr_path.read_text(encoding='utf-8')

        # Matches both ASCII colon and full-width colon
        metrics = re.findall(r'\*\*([^\*]+)\*\*:(.+?)(?:\n|$)', content)

        return {
            'metrics': [(k.strip(), v.strip()) for k, v in metrics],
            'content_preview': content[:500]
        }

    return {}


def parse_risk_register(repo_path: Path) -> List[Dict]:
    """Parse RISK_REGISTER.md"""
    rr_paths = [
        repo_path / "07-risk" / "RISK_REGISTER.md",
    ]

    for rr_path in rr_paths:
        if not rr_path.exists():
            continue

        content = rr_path.read_text(encoding='utf-8')

        risk_pattern = re.compile(r'\|\s*([^\|]+)\s*\|.*?\|.*?\|.*?\|', re.MULTILINE)
        risks = []
        for m in risk_pattern.finditer(content):
            risk_name = m.group(1).strip()
            if risk_name and len(risk_name) > 3:
                risks.append({'name': risk_name})

        if risks:
            return risks[:20]

    return []


def parse_config_records(repo_path: Path) -> List[Dict]:
    """Parse CONFIG_RECORDS.md"""
    cr_paths = [
        repo_path / "08-config" / "CONFIG_RECORDS.md",
    ]

    for cr_path in cr_paths:
        if not cr_path.exists():
            continue

        content = cr_path.read_text(encoding='utf-8')

        config_pattern = re.compile(r'\|[ \t]*([^\|\n]+)[ \t]*\|[^\|\n]*?\|[^\|\n]*?\|', re.MULTILINE)
        configs = []
        for m in config_pattern.finditer(content):
            config_name = m.group(1).strip()
            if config_name and len(config_name) > 3:
                configs.append({'name': config_name})

        if configs:
            return configs[:20]

    return []


def parse_srs_fr_nfr_xref(srs_path) -> Dict[str, List[str]]:
    """Parse the FR Cross-Reference table in SRS.md §2 to extract NFR associations.

    Many SRS documents store FR-to-NFR mapping in a dedicated cross-reference
    table with an 'NFR Association' column (rather than embedding NFR IDs inside
    individual FR descriptions).  This function finds that table and returns a
    ``{fr_id: [nfr_id, ...]}`` mapping so the plan generator can produce the
    correct NFR Coverage section.

    Returns {} when the table is absent or cannot be parsed.
    """
    if srs_path is None:
        return {}
    srs_path = Path(srs_path)
    if not srs_path.exists():
        return {}

    content = srs_path.read_text(encoding="utf-8")

    # Locate the table header that contains 'NFR Association' (case-insensitive).
    header_re = re.compile(r'^(?:\|[^|\n]*)+\|\s*NFR\s*Association\s*\|', re.IGNORECASE | re.MULTILINE)
    header_match = header_re.search(content)
    if not header_match:
        return {}

    # Determine which column index holds 'NFR Association'.
    header_line = header_match.group(0)
    cols = [c.strip() for c in header_line.split('|') if c.strip()]
    nfr_col_idx = next(
        (i for i, c in enumerate(cols) if 'nfr' in c.lower() and 'assoc' in c.lower()),
        -1,
    )
    if nfr_col_idx == -1:
        return {}

    # Parse rows that immediately follow the header (stop at blank line or new section).
    fr_nfr_map: Dict[str, List[str]] = {}
    rest = content[header_match.end():]
    for line in rest.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            if line:
                break   # non-table content → end of table
            continue
        # Skip separator rows (|---|---|)
        if re.match(r'^\|[\s\-|]+\|$', line):
            continue
        cells = [c.strip() for c in line.split('|') if c.strip()]
        if not cells:
            continue
        # First cell must be a bare FR-XX id
        fr_match = re.match(r'^(FR-\d+)$', cells[0])
        if not fr_match:
            continue
        fr_id = f"FR-{fr_match.group(1).split('-')[1].zfill(2)}"
        if nfr_col_idx < len(cells):
            nfr_ids = [f"NFR-{n.zfill(2)}" for n in re.findall(r'NFR-(\d+)', cells[nfr_col_idx])]
            if nfr_ids:
                fr_nfr_map[fr_id] = nfr_ids

    return fr_nfr_map


def parse_srs_nfr_sections(srs_path: Optional[Path]) -> List[Dict]:
    """Parse SRS.md to extract NFR sections"""
    if srs_path is None:
        return []
    srs_path = Path(srs_path)
    if not srs_path.exists():
        return []

    content = srs_path.read_text(encoding='utf-8')

    # Note: pattern uses full-width colon to match Chinese-formatted SRS
    nfr_pattern = re.compile(r'(### NFR-(\d+):[^\n]+\n\n)(.*?)(?=\n---\n|\n###|\n##|\Z)', re.DOTALL)

    nfrs = []
    for m in nfr_pattern.finditer(content):
        nfr_num = f"NFR-{m.group(2).zfill(2)}"
        title = m.group(1).strip().split('\n')[0].replace('### ', '')
        details = m.group(3).strip()[:400]

        nfrs.append({
            'nfr': nfr_num,
            'title': title,
            'details': details
        })

    # Fallback: table-format NFR extraction (| NFR-01 | Performance | desc | method |)
    if not nfrs:
        table_re = re.compile(
            r'^\|\s*NFR-(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|',
            re.MULTILINE,
        )
        seen = set()
        for m in table_re.finditer(content):
            nfr_num = f"NFR-{m.group(1).zfill(2)}"
            if nfr_num in seen:
                continue
            seen.add(nfr_num)
            nfr_type = m.group(2).strip()
            desc = m.group(3).strip()
            if len(desc) > 400:
                desc = desc[:397] + "..."
            nfrs.append({
                'nfr': nfr_num,
                'title': f"NFR-{m.group(1).zfill(2)}: {nfr_type}",
                'details': desc,
            })

    if not nfrs:
        print(
            "[generate_full_plan] WARNING: No NFR sections found in SRS.md.\n"
            "  Expected format: '### NFR-01: Title' sections or '| NFR-01 | Type | desc |' table rows.\n"
            "  The generated plan will have no NFR summary section.",
            file=sys.stderr,
        )

    return nfrs


# ============================================================================
# Gate Step Helpers (two-phase evaluation: run-gate → evaluate → finalize-gate)
# ============================================================================

# Phase → gate applicability
_PHASE_GATE1_PHASES: frozenset = frozenset({3, 4, 5, 7, 8})   # Gate 1 per-FR
_PHASE_EXIT_GATES: dict = {3: 2, 4: 3, 6: 4}                  # phase → exit gate num

# 10-Push Strategy labels for the P1/P2 checkpoint pushes (① ②).
# Pushes ③–⑩ are emitted by _milestone_push_steps / _gate_exit_checkpoint;
# ① and ② are the P1/P2 Agent-B-review checkpoint pushes.
_PHASE_PUSH_LABELS: dict = {1: "PUSH ① — ", 2: "PUSH ② — "}

# Gate metadata: (score_gate, dim_count, notes)
_GATE_META: dict = {
    1: (None, 3,  "linting(90) · type_safety(85) · test_coverage(80)"),
    2: (75,   10, "linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · integration_coverage(60) · test_assertion_quality(60) · traceability(100)  [traceability: framework-owned, harness-computed · D4 spec-coverage unified ≥60%]"),
    3: (80,   15, "linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · integration_coverage(60) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · test_assertion_quality(60) · performance(75) · traceability(100)  [traceability: framework-owned, harness-computed · CRG recon inside run-gate · D4 spec-coverage unified ≥80%]"),
    4: (85,   15, "linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · performance(75) · integration_coverage(75) · test_assertion_quality(70) · traceability(100)  [traceability: framework-owned, harness-computed · CRG recon inside run-gate · D4 spec-coverage unified ≥90%]"),
}

# D4 spec-coverage-check thresholds per exit gate (unified v2.6)
_SPEC_COVERAGE_THRESHOLDS: dict = {2: 60.0, 3: 80.0, 4: 90.0}

# A/B agent roles per phase: (Agent-A role, Agent-B role, task hint)
_PHASE_ROLES: dict = {
    1: ("REQUIREMENTS_ENGINEER", "BUSINESS_ANALYST", "Elicit requirements → write FRs/NFRs in SRS.md (### FR-XX: format) → validate completeness"),
    2: ("ARCHITECT",   "TECH_LEAD",   "Design system architecture → write SAD.md + ADR.md → validate every FR has a module mapping"),
    3: ("DEVELOPER",   "REVIEWER",    "TDD: write failing test → implement → refactor (RED→GREEN→IMPROVE)"),
    4: ("QA_ENGINEER", "ARCHITECT",   "Execute TEST_PLAN.md test cases for this FR → record results in TEST_RESULTS.md → verify ≥80% branch coverage"),
    5: ("DEVELOPER",   "REVIEWER",    "Verify FR acceptance criteria → confirm results match SRS → sign off"),
    6: ("QA_ENGINEER", "ARCHITECT",   "Full project quality audit → identify remaining issues → write QUALITY_REPORT.md → achieve Gate4 (≥85)"),
    7: ("DEVOPS",      "ARCHITECT",   "Identify risks → document likelihood/impact → define mitigations"),
    8: ("DEVOPS",      "ARCHITECT",   "Document config items → verify env vars/secrets → update CONFIG_RECORDS.md"),
}

# Agent B embed doc list per phase — ALL must be pasted verbatim into the prompt (no file paths)
_AGENT_B_EMBED_DOCS: Dict[int, List[str]] = {
    1: ["Project description / stakeholder brief", "draft 01-requirements/SRS.md (full content)"],
    2: ["01-requirements/SRS.md (full)", "draft 02-architecture/SAD.md (full)"],
    3: ["01-requirements/SRS.md §FR-XX section", "02-architecture/SAD.md module spec for FR-XX", "03-development/src/…/fr_xx.py (implemented code + tests)"],
    4: ["01-requirements/SRS.md §FR-XX section", "02-architecture/SAD.md module spec", "03-development/src/…/fr_xx.py", "tests/…/test_fr_xx.py", "04-testing/TEST_PLAN.md entry for FR-XX"],
    5: ["01-requirements/SRS.md acceptance criteria for FR-XX", "03-development/src/…/fr_xx.py", "tests/…/test_fr_xx.py", "04-testing/TEST_RESULTS.md entry"],
    6: ["01-requirements/SRS.md", "02-architecture/SAD.md", "06-quality/QUALITY_REPORT.md (draft)", "relevant 03-development/src/ sections"],
    7: ["01-requirements/SRS.md §FR-XX section", "07-risk/RISK_REGISTER.md (FR-XX draft entry)", "06-quality/QUALITY_REPORT.md (FR-XX findings)"],
    8: ["01-requirements/SRS.md §FR-XX section", "08-config/CONFIG_RECORDS.md (FR-XX draft entry)", "03-development/src/.../fr_xx.py"],
}

# Agent B review checklist per phase
_AGENT_B_CHECKS: Dict[int, List[str]] = {
    1: ["All FRs testable? (no vague criteria)", "NFRs measurable?", "No contradictions between FRs?", "Every stakeholder need covered?"],
    2: ["Every FR maps to ≥1 module?", "NFRs addressed (latency/security/cost)?", "No circular dependencies?", "ADR covers all major decisions?"],
    3: ["Code matches SRS acceptance criteria?", "Tests actually test the spec (not the impl)?", "No forbidden patterns (app/infrastructure/, @covers: L1 Error)?", "Docstrings have [FR-XX] tag + Citations?"],
    4: ["Test coverage ≥80% for this FR?", "Edge cases covered?", "Results match TEST_PLAN.md expected outcomes?"],
    5: ["Acceptance criteria fully met?", "No regressions in related FRs?"],
    6: ["All 14 Gate 4 dimensions addressed?", "Critical issues count = 0?", "Score ≥85 achievable?"],
    7: ["All high-risk items have mitigations?", "Likelihood/impact scores justified?"],
    8: ["All config items documented?", "Secrets correctly externalized?", "No hardcoded credentials?"],
}


# Per-phase deliverable dependency chains for task decomposition.
# Keys: label, desc, depends_on, task_hint, checks, embed_docs
# depends_on lists deliverable labels within the same phase that must be APPROVED first.
_PHASE_DELIVERABLE_DEPS: Dict[int, List[Dict]] = {
    1: [
        {
            "label": "SRS.md",
            "desc": "Software Requirements Specification — functional + non-functional requirements",
            "depends_on": [],
            "task_hint": "Elicit requirements → write FRs/NFRs in SRS.md (### FR-XX: format) → validate completeness",
            "checks": ["All FRs testable? (no vague criteria)", "NFRs measurable?",
                       "No contradictions between FRs?", "Every stakeholder need covered?"],
            "embed_docs": ["Project description / stakeholder brief", "draft 01-requirements/SRS.md (full content)"],
        },
        {
            "label": "SPEC_TRACKING.md",
            "desc": "Spec Tracking Matrix — maps every FR to its current status, owner, and acceptance state",
            "depends_on": ["SRS.md"],
            "task_hint": "Build spec tracking matrix from SRS.md FRs → assign status/owner per FR → validate completeness",
            "checks": ["Every FR from SRS.md listed?", "Status field populated per FR?",
                       "Owner assigned per FR?", "No orphan FRs (in SRS but not tracked)?"],
            "embed_docs": ["01-requirements/SRS.md (APPROVED — full content)",
                           "draft 01-requirements/SPEC_TRACKING.md (full content)"],
        },
        {
            "label": "TRACEABILITY_MATRIX.md",
            "desc": "Requirements Traceability Matrix — bidirectional traceability from FRs through design to tests",
            "depends_on": ["SRS.md", "SPEC_TRACKING.md"],
            "task_hint": "Build bidirectional traceability matrix → link FRs → design elements → test cases → validate coverage",
            "checks": ["Bidirectional traceability established? (FR→design→test and back)",
                       "Every FR has ≥1 downstream link?", "No orphan requirements?",
                       "Coverage complete (all FRs traceable)?"],
            "embed_docs": ["01-requirements/SRS.md (APPROVED — full content)",
                           "01-requirements/SPEC_TRACKING.md (APPROVED — full content)",
                           "draft 01-requirements/TRACEABILITY_MATRIX.md (full content)"],
        },
        {
            "label": "TEST_INVENTORY.yaml",
            "desc": "Test Inventory — P1 naming authority, feeds TEST_SPEC.md (D4 unified source)",
            "depends_on": ["TRACEABILITY_MATRIX.md"],
            "task_hint": "Generate TEST_INVENTORY.yaml from SRS.md FR acceptance criteria → assign test function names per FR → validate naming convention",
            "checks": ["Every FR has ≥1 test function?", "Test function names follow naming convention?",
                       "All FRs from TRACEABILITY_MATRIX covered?"],
            "embed_docs": ["01-requirements/SRS.md (APPROVED — full content)",
                           "01-requirements/TRACEABILITY_MATRIX.md (APPROVED — full content)",
                           "draft TEST_INVENTORY.yaml (full content)"],
        },
    ],
    2: [
        {
            "label": "SAD.md",
            "desc": "Software Architecture Document — components, interfaces, FR→module mapping, data flows",
            "depends_on": [],
            "task_hint": "Design system architecture → write SAD.md → validate every FR has a module mapping",
            "checks": ["Every FR maps to ≥1 module?", "NFRs addressed (latency/security/cost)?",
                       "No circular dependencies?", "Data flow diagrams consistent?"],
            "embed_docs": ["01-requirements/SRS.md (full)",
                           "draft 02-architecture/SAD.md (full)"],
        },
        {
            "label": "ADR.md",
            "desc": "Architecture Decision Records — document key design decisions (tech stack, patterns, interfaces, trade-offs) with context and consequences",
            "depends_on": ["SAD.md"],
            "task_hint": "Extract key architecture decisions from SAD.md → write individual ADR entries → validate rationale and consequences are recorded",
            "checks": ["All major decisions documented (tech stack, patterns, interfaces)?",
                       "Each ADR has clear context, decision, and consequences?",
                       "Alternatives considered documented?",
                       "Decision aligns with SAD.md architecture?"],
            "embed_docs": ["02-architecture/SAD.md (APPROVED — full content)",
                           "draft 02-architecture/ADR.md (full content)",
                           "templates/ADR.md (template format)"],
        },
        {
            "label": "TEST_SPEC.md",
            "desc": "Test Specification Catalog — named test cases from SRS (single source of truth, D4 unified check)",
            "depends_on": ["ADR.md"],
            "task_hint": "Generate TEST_SPEC.md via derive_test_cases.md skill → preserve TEST_INVENTORY.yaml names where specified → apply 7-Question Protocol per FR → populate cross-cutting section",
            "checks": ["Every FR has ≥1 named test case?", "7-Question Protocol applied per FR?",
                       "Cross-cutting section complete?", "Summary table populated?"],
            "embed_docs": ["01-requirements/SRS.md (APPROVED — full content)",
                           "02-architecture/SAD.md (APPROVED — full content)",
                           "02-architecture/ADR.md (APPROVED — full content)",
                           "draft 02-architecture/TEST_SPEC.md (full content)"],
        },
    ],
}


def _agent_b_dispatch_block(phase: int, role_b: str, fr_id: str = "") -> List[str]:
    """
    Generate the full Agent B stateless dispatch block for a given phase.

    Key principle: Agent B = completely fresh MCP sandbox with ZERO filesystem access.
    Every document must be embedded verbatim in the prompt — never use file paths.
    This is the #1 lesson from P1 Rounds 2-3 failures.
    """
    embed_docs = _AGENT_B_EMBED_DOCS.get(phase, ["relevant documents"])
    checks = _AGENT_B_CHECKS.get(phase, ["Review for correctness and completeness"])
    fr_suffix = f" for {fr_id}" if fr_id else ""
    _task_obj = {7: "risk assessment", 8: "configuration record"}.get(phase, "deliverable")

    lines: List[str] = [
        f"- [ ] **[B-1]** Agent B ({role_b}){fr_suffix} — dispatch as **STATELESS** subagent:",
        "  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.",
        "  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.",
        "  > ALL context must be pasted verbatim into the prompt text. This is mandatory.",
        "  >",
        "  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.",
        "  > Round 4 succeeded only after embedding full document content directly.",
        "",
        "  **Embed these documents in full** (copy content, not paths):",
    ]
    for doc in embed_docs:
        lines.append(f"  - `{doc}`")
    lines += [
        "",
        "  **Agent B prompt structure** (use this template verbatim):",
        "  ```",
        f"  You are {role_b}. Your task: review the following {_task_obj}{fr_suffix}.",
        "  You have NO access to any files — all context is provided below.",
        "",
    ]
    # Auto-enumerate actual doc titles so the agent doesn't have to map them manually
    for i, doc in enumerate(embed_docs, 1):
        lines += [
            f"  === [DOC {i}: {doc}] ===",
            "  <<paste full content here>>",
            "",
        ]
    lines += [
        "  Review checklist:",
    ]
    for check in checks:
        lines.append(f"  - {check}")
    _docs_hint = {1: '["SRS.md"]', 2: '["SRS.md", "SAD.md"]'}.get(
        phase, '["<basename of each source doc embedded above>"]'
    )
    lines += [
        "",
        "  Return JSON only:",
        '  {"review_status":"APPROVE"|"REJECT",',
        '   "reason":"<concise summary>",',
        '   "citations":["file:line"],',
        f'   "docs_embedded":{_docs_hint},',
        '   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}',
        "  ```",
        "",
        "- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:",
        "  > gaps schema: `[{\"severity\": \"low|medium|high\", \"message\": \"...\", \"fr_id\": \"FR-XX or null\"}]`",
        "  - `APPROVE` + all gaps are `low` → proceed to push (CHECKPOINT saved)",
        "  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**",
        "    (embed same docs as B-1 above with updated content) → push only after round-2 APPROVE",
        "  - `REJECT` → fix all gaps → re-dispatch B. Max 5 rounds (HR-12).",
        "    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.",
        "    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.",
        "",
    ]
    return lines


def _decomposition_section(phase: int) -> List[str]:
    """Generate the task decomposition + dependency analysis section for a phase.

    Lists all deliverables in dependency order and declares the execution rule:
    each deliverable must pass Agent B review before the next one starts.
    """
    deliverables = _PHASE_DELIVERABLE_DEPS.get(phase, [])
    if not deliverables:
        return []

    role_a, role_b, _ = _PHASE_ROLES.get(phase, ("DEVELOPER", "REVIEWER", ""))
    total = len(deliverables)

    lines = [
        "### Task Decomposition (Dependency Analysis)",
        "",
        f"**Phase {phase} has {total} deliverables with sequential dependencies:**",
        "",
        "| Order | Deliverable | Depends On | Agent A | Agent B |",
        "|-------|------------|------------|---------|---------|",
    ]
    for i, d in enumerate(deliverables, 1):
        deps = ", ".join(d["depends_on"]) if d["depends_on"] else "(none — starting point)"
        lines.append(f"| {i} | `{d['label']}` | {deps} | {role_a} | {role_b} |")

    lines += [
        "",
        "**Execution rule**: Each deliverable must pass Agent B review BEFORE starting the next.",
        "If a deliverable is REJECTED, fix only that deliverable — earlier APPROVED deliverables",
        "are not re-opened. This bounds backtracking to a single step.",
        "",
    ]
    return lines


def _deliverable_ab_block(phase: int, deliverable: Dict, sub_n: int, total: int,
                          label_to_sub_n: Optional[Dict[str, int]] = None) -> List[str]:
    """Generate the A/B collaboration block for a single deliverable.

    Produces A-1/A-2/B-1/B-2/LOG steps with deliverable-specific checks and embed docs.
    label_to_sub_n maps deliverable label → sub-task number for correct B-2 review chaining.
    """
    role_a, role_b, _ = _PHASE_ROLES.get(phase, ("DEVELOPER", "REVIEWER", ""))
    label = deliverable["label"]
    desc = deliverable["desc"]
    deps = ", ".join(deliverable["depends_on"]) if deliverable["depends_on"] else "none — starting point"
    checks = list(deliverable.get("checks", _AGENT_B_CHECKS.get(phase, ["Review for correctness"])))
    embed_docs = list(deliverable.get("embed_docs", _AGENT_B_EMBED_DOCS.get(phase, ["relevant documents"])))
    task_hint = deliverable["task_hint"]
    is_first = sub_n == 1
    is_last = sub_n == total
    lmap = label_to_sub_n or {}

    # Non-first sub-tasks: embed B-2 reviews from the deliverables this one
    # actually depends_on (not just the immediately preceding sub-task).
    if not is_first:
        dep_entries = [(lmap[dep], dep) for dep in deliverable["depends_on"] if dep in lmap]
        dep_entries.sort(key=lambda x: x[0])
        # Insert in reverse order so earliest sub-task ends up at position 0
        for sub_n_dep, dep_label in reversed(dep_entries):
            embed_docs.insert(
                0,
                f"Previous Sub-Task B-2 review JSON — {dep_label} "
                f"(Sub-Task {sub_n_dep}/{total}, gaps field may contain non-blocking caveats)",
            )
        checks.insert(0, "Upstream deliverable review caveats addressed? (check previous B-2 gaps field)")

    # Final sub-task: integration consistency check across all upstream deliverables.
    if is_last and total > 1:
        checks.append("All upstream deliverables consistent with each other? No contradictory decisions?")

    dep_note = ""
    if not is_first:
        dep_nums = [lmap[dep] for dep in deliverable["depends_on"] if dep in lmap]
        if dep_nums:
            dep_refs = ", ".join(f"{n}/{total}" for n in sorted(dep_nums))
            dep_note = f" (+ Sub-Task {dep_refs} review: previous review gaps carry forward)"
    lines = [
        f"### Sub-Task {sub_n}/{total}: {label} — {desc}",
        "",
        f"**Depends on**: {deps}{dep_note}",
        f"**Agent A**: {role_a}",
        f"**Agent B**: {role_b}",
        "",
        "**A/B Work** (HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews):",
        f"- [ ] **[A-1]** Agent A ({role_a}): {task_hint}",
        "  - FORBIDDEN: vague/non-testable acceptance criteria",
        "- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`",
    ]

    # Agent B stateless dispatch block (customized per deliverable)
    lines += [
        f"- [ ] **[B-1]** Agent B ({role_b}) — dispatch as **STATELESS** subagent:",
        "  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.",
        "  > NEVER write 'read 01-requirements/SRS.md' in the prompt — it will fail silently.",
        "  > ALL context must be pasted verbatim into the prompt text. This is mandatory.",
        "  >",
        "  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.",
        "  > Round 4 succeeded only after embedding full document content directly.",
        "",
        "  **Embed these documents in full** (copy content, not paths):",
    ]
    for doc in embed_docs:
        lines.append(f"  - `{doc}`")
    lines += [
        "",
        "  **Agent B prompt structure** (use this template verbatim):",
        "  ```",
        f"  You are {role_b}. Your task: review the following deliverable ({label}).",
        "  You have NO access to any files — all context is provided below.",
        "",
    ]
    for i, doc in enumerate(embed_docs, 1):
        lines += [
            f"  === [DOC {i}: {doc}] ===",
            "  <<paste full content here>>",
            "",
        ]
    lines += [
        "  Review checklist:",
    ]
    for check in checks:
        lines.append(f"  - {check}")
    _docs_hint = {1: '["SRS.md"]', 2: '["SRS.md", "SAD.md"]'}.get(
        phase, '["<basename of each source doc embedded above>"]'
    )
    lines += [
        "",
        "  Return JSON only:",
        '  {"review_status":"APPROVE"|"REJECT",',
        '   "reason":"<concise summary>",',
        '   "citations":["file:line"],',
        f'   "docs_embedded":{_docs_hint},',
        '   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}',
        "  ```",
        "",
        "- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:",
        "  > gaps schema: `[{\"severity\": \"low|medium|high\", \"message\": \"...\", \"fr_id\": \"FR-XX or null\"}]`",
    ]
    if sub_n < total:
        next_action = f"continue to Sub-Task {sub_n + 1}/{total}"
    else:
        next_action = "all deliverables complete; proceed to Agent B Peer Review"
    lines += [
        f"  - `APPROVE` + all gaps are `low` → {next_action}",
        "  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**",
        f"    (embed same docs as B-1 above, replacing `{label}` with its updated content)",
        f"    → {next_action} only after round-2 APPROVE",
        "  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).",
        "    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.",
        "    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.",
        "",
        "  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current",
        "  > round is fully APPROVED (including any required round 2).",
        "  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).",
        "",
        f"  > fr_id uses P{phase} as phase-level placeholder; replace with FR-XX for FR-specific plans.",
        "",
    ]
    return lines


def _preflight_steps(phase: int) -> List[str]:
    """Preflight hook step — run before the FR development loop (FSM + Constitution check + CI readiness)."""
    if phase == 1:
        ci_check = [
            "- [ ] **[PREFLIGHT-CI]** Verify CI wiring (all 3 items auto-set by `init-project`):",
            "  1. `.methodology/state.json` exists with `current_phase = 1`",
            "  2. `.github/workflows/harness_quality_gate.yml` exists in project root",
            "  3. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)",
            "  4. Phase stored in `.methodology/state.json` — single source of truth (no GitHub variable needed)",
            "  If any item (1-3) is missing — run automated fix:",
            "  ```bash",
            "  python3 harness_cli.py init-project --phase 1 --project .",
            "  ```",
            "  Re-verify items 1-3 after running.",
            "  If still failing after `init-project`: escalate to human — provide `init-project` error output.",
        ]
    else:
        ci_check = [
            "- [ ] **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):",
            "  1. `.github/workflows/harness_quality_gate.yml` exists",
            "  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)",
            "  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)",
            f"  4. Phase {phase} confirmed in `.methodology/state.json` (`advance-phase` already run)",
            f"  > If stale: run `python3 harness_cli.py init-project --phase {phase} --project . --overwrite`",
        ]
    return [
        "### Pre-Phase Preflight",
        "",
        "- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Constitution, Kill-Switch, Drift, CI Readiness):",
        "  ```bash",
        f"  python3 harness_cli.py run-phase --phase {phase} --project .",
        "  ```",
        "  If FAILED: fix FSM/Constitution/Drift issues. There is no gate bypass flag.",
        "  Re-run `run-phase` after each fix. Max 3 attempts.",
        f"  After 3 FAIL: escalate to human — provide last `run-phase --phase {phase}` full output.",
        f"  Human fix → re-run `run-phase --phase {phase} --project .` → PASS required before continuing.",
        *([] if phase < 5 else [
            "  **Attestation fix** (P5+ — if ASPICE Traceability preflight shows `attestation: missing` or `mismatch`):",
            "  ```bash",
            "  python3 harness_cli.py build-trace-attestation --project . --write",
            "  git add .methodology/trace/attestation.json",
            "  git commit -m 'trace: regenerate attestation'",
            "  ```",
            "  Re-run `run-phase` to confirm `Attestation: clean` before continuing.",
        ]),
        "",
        *ci_check,
        "",
    ]


def _entry_gate_check(phase: int) -> List[str]:
    """Entry condition check — confirm previous phase exit gate before starting any work."""
    _ENTRY_MAP: dict = {
        2: ("P1 review-complete",
            "git log contains commit 'phase1(review-complete): Phase 1 deliverables APPROVED'"),
        3: ("P2 review-complete",
            "git log contains commit 'phase2(review-complete): Phase 2 deliverables APPROVED'"),
        4: ("Gate 2 PASS",
            ".methodology/quality_manifest.json records Gate 2 PASS from P3"),
        5: ("Gate 3 PASS",
            ".methodology/quality_manifest.json records Gate 3 PASS from P4"),
        6: ("Gate 3 PASS (P4 exit — P5 has no exit gate, P5 completed stands between)",
            ".methodology/quality_manifest.json records Gate 3 PASS from P4"),
        7: ("Gate 4 PASS",
            ".methodology/quality_manifest.json records Gate 4 PASS from P6"),
        8: ("Gate 4 PASS (P6 exit — P7 has no exit gate, P7 completed stands between)",
            ".methodology/quality_manifest.json records Gate 4 PASS from P6"),
    }
    if phase not in _ENTRY_MAP:
        return []
    gate_label, proof = _ENTRY_MAP[phase]
    # Parse correct predecessor phase from proof string (e.g. "from P4" → 4)
    # Naive phase-1 is wrong when entry gate jumps over a phase (P6 needs P4, not P5)
    m = re.search(r'from P(\d+)', proof)
    prev_phase = int(m.group(1)) if m else phase - 1
    gate_action = f"return to Phase {prev_phase} and complete exit gate first" if prev_phase >= phase - 2 else f"verify Phase {prev_phase} Gate PASS is recorded in quality_manifest.json and confirm all intervening phases (P{prev_phase+1}–P{phase-1}) completed their tasks"
    lines = [
        "### Entry Gate Verification",
        "",
        f"- [ ] **[ENTRY-CHECK]** {gate_label}:",
        f"  Proof: {proof}.",
        f"  If NOT confirmed: {gate_action}.",
        "",
    ]
    # Phase 2 entry: explicitly verify all 4 P1 deliverables per CONSTITUTION.md §2.3
    if phase == 2:
        lines.extend([
            "- [ ] **[P1-ARTIFACTS]** Verify all 4 Phase 1 deliverables exist (CONSTITUTION.md §2.3 P2 entry requirement):",
            "  ```bash",
            "  ls 01-requirements/SRS.md \\",
            "     01-requirements/SPEC_TRACKING.md \\",
            "     01-requirements/TRACEABILITY_MATRIX.md \\",
            "     TEST_INVENTORY.yaml",
            "  ```",
            "  All 4 files must exist. If any is missing → return to Phase 1 to complete them before entering Phase 2.",
            "",
        ])
    # Phase 3 entry: verify P2 output artifacts exist before starting implementation
    if phase == 3:
        lines.extend([
            "- [ ] **[P2-ARTIFACTS]** Verify Phase 2 output artifacts exist:",
            "  ```bash",
            "  ls -la 02-architecture/SAD.md 02-architecture/ADR.md 02-architecture/TEST_SPEC.md \\",
            "     .methodology/quality_manifest.json .methodology/SAB.json",
            "  git log --oneline --grep=\"APPROVE\" -1",
            "  ```",
            "  If any file missing: return to Phase 2 and complete missing deliverables.",
            "",
        ])
    # Phase 6 entry: also verify P5 output artifacts per SAD.md §2.4.3
    if phase == 6:
        lines.insert(3, "  Verify P5 output artifacts exist: `05-verification/VERIFICATION_REPORT.md` + `05-verification/BASELINE.md`")
    return lines


def _review_checkpoint(phase: int, checkpoint_n: int) -> List[str]:
    """Agent B peer-review checkpoint for P1/P2 (deliverable review — NOT harness run-gate).

    Agent B is dispatched as a STATELESS sub-agent (same pattern as inline [B-1][B-2]).
    [B-1] = dispatch Agent B with ALL deliverables embedded in prompt (no file paths)
    [B-2] = orchestrator parses Agent B's JSON response (APPROVE/REJECT)
    [B-PUSH] = orchestrator runs push-checkpoint after APPROVE
    """
    _DELIVERABLES: dict = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md",
            "01-requirements/TRACEABILITY_MATRIX.md",
            "TEST_INVENTORY.yaml"],          # project root — D4 reads from here
        2: ["02-architecture/SAD.md", "02-architecture/ADR.md",
            "02-architecture/TEST_SPEC.md"],
    }
    artifacts = _DELIVERABLES.get(phase, [])
    _, role_b, _ = _PHASE_ROLES.get(phase, ("DEVELOPER", "REVIEWER", ""))

    lines: List[str] = [
        "",
        f"### 🔒 CHECKPOINT-PEER-REVIEW: Agent B Peer Review — Phase {phase} Exit",
        "> Phase 1/2 exit gate = Agent B document review (NOT `harness run-gate --gate 1`).",
        "> APPROVE criteria: all FRs addressed, no critical gaps, terminology consistent.",
        "",
        f"- [ ] **[B-1]** Agent B ({role_b}) — dispatch as **STATELESS** subagent (holistic review of all deliverables):",
        "  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.",
        "  > NEVER pass file paths in the prompt — ALL document content must be pasted verbatim.",
        "  >",
        "  > **Lesson (stateless agent)**: Rounds 2-3 failed because prompts used file paths.",
        "  > Round 4 succeeded only after embedding full document content directly.",
        "",
        "  **Embed ALL deliverables in full** (copy content, not paths):",
        *(["  > Note: `quality_manifest.json` and `SAB.json` are machine-generated by `generate_sab.py`",
           "  > and are NOT embedded for manual review. Agent B reviews the human-authored documents only.",
           ] if phase == 2 else []),
    ]
    for artifact in artifacts:
        lines.append(f"  - `{artifact} (full content)`")
    lines += [
        "",
        "  **Agent B prompt structure** (use this template verbatim):",
        "  ```",
        f"  You are {role_b}. Your task: holistic review of ALL Phase {phase} deliverables.",
        "  You have NO access to any files — all context is provided below.",
        "",
    ]
    for i, artifact in enumerate(artifacts, 1):
        lines += [
            f"  === [DOC {i}: {artifact}] ===",
            "  <<paste full content here>>",
            "",
        ]
    lines += [
        "  Review checklist:",
        "  - All FRs covered across all deliverables?",
        "  - No contradictions between deliverables?",
        "  - Each item testable/traceable?",
        "  - All gaps from sub-task reviews addressed?",
        "  - Terminology consistent across all documents?",
        "",
    ]
    _docs_hint = {1: '["SRS.md"]', 2: '["SRS.md", "SAD.md"]'}.get(
        phase, '["<basename of each source doc embedded above>"]'
    )
    lines += [
        "  Return JSON only:",
        '  {"review_status":"APPROVE"|"REJECT",',
        '   "reason":"<concise summary>",',
        '   "citations":["file:line"],',
        f'   "docs_embedded":{_docs_hint},',
        '   "gaps":[{"severity":"low|medium|high","message":"<issue>","fr_id":"<FR-XX or null>"}]}',
        "  ```",
        "",
        "- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:",
        "  - `APPROVE` + all gaps are `low` → proceed to push (CHECKPOINT saved)",
        "  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**",
        "    (embed same docs as B-1 above with updated content) → push only after round-2 APPROVE",
        "  - `REJECT` → fix all gaps → re-dispatch B. Max 5 rounds (HR-12).",
        "    > If round 5 REJECT: escalate to human — orchestrator cannot self-resolve.",
        "    > Human fix → re-dispatch Agent B (same prompt + updated content) → `APPROVE` required before continuing.",
        "",
        f"- [ ] **[B-PUSH]** ✅ {_PHASE_PUSH_LABELS.get(phase, '')}Push to GitHub + HANDOVER.md — retry until success (CHECKPOINT-PEER-REVIEW saved):",
        "  > Run `push-checkpoint` → if blocked, read the error → fix → re-run until green.",
        "  > Do NOT use `--no-verify` to bypass.",
        "  ```bash",
        f"  python3 harness_cli.py push-checkpoint --phase {phase} --project .",
        "  ```",
        "  > This writes `HANDOVER.md` (crash-recovery checkpoint) to project root,",
        "  > then commits + pushes all changes to origin.",
        "  > After a crash, read HANDOVER.md first — it tells you where you were.",
        "",
    ]
    return lines



def _fr_dev_steps(fr_id: str, phase: int) -> List[str]:
    """Per-FR implementation steps.

    Phase 1-2: A/B collaboration (Agent A + Agent B with dispatch).
    Phase 3-8: Direct implementation (no A/B — Phase End Audit替代).
    """
    if phase >= 4:
        return _fr_carryforward_steps(fr_id, phase)

    if phase <= 2:
        role_a, role_b, task_hint = _PHASE_ROLES.get(
            phase, ("DEVELOPER", "REVIEWER", "Implement per SRS + SAD")
        )
        lines = [
            f"**A/B Work — {fr_id}** (HR-04: Agent A authors, a separate Agent B sub-agent reviews):",
            f"- [ ] **[A-1]** Agent A ({role_a}): {task_hint}",
            f"  - Docstrings: `[{fr_id}]` tag + `Citations:` with line numbers (HR-15)",
            "  - FORBIDDEN: `app/infrastructure/` · `@covers: L1 Error` · `@type: edge`",
            "- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`",
            "- [ ] **[A-DISPATCH]** Dispatch Agent A:",
            "  ```bash",
            f"  python3 harness_cli.py dispatch --role developer --fr-id {fr_id} \\",
            f"    --prompt \"{task_hint} for {fr_id}\" --phase {phase} --project .",
            "  ```",
        ]
        lines.extend(_agent_b_dispatch_block(phase, role_b, fr_id=fr_id))
        lines.extend([
            "- [ ] **[B-DISPATCH]** Dispatch Agent B:",
            "  ```bash",
            f"  python3 harness_cli.py dispatch --role reviewer --fr-id {fr_id} \\",
            f"    --prompt \"Review {fr_id} against SRS + SAD\" --phase {phase} --project .",
            "  ```",
            "  > AgentSpawner records dispatches to `.methodology/sessions_spawn.log` (non-blocking debug trail).",
            "",
        ])
        return lines

    # Phase 3-8: Orchestrator dispatches sub-agents per step (no direct execution).
    # Each run-fr-step call: builds need-to-know context → AgentSpawner.spawn() →
    # claude -p sub-agent → verify → git push (GitHub crash-recovery checkpoint).
    num = re.match(r"FR-(\d+)", fr_id)
    num_str = num.group(1).zfill(2) if num else re.sub(r"[^a-z0-9]", "_", fr_id.lower()).strip("_")
    srs_flag = " --srs 01-requirements/SRS.md"
    return [
        f"**TDD — {fr_id}** (Orchestrator dispatches sub-agents · push after each step):",
        "",
        f"- [ ] **[ORCH-RED]** Dispatch TDD-RED sub-agent for {fr_id}:",
        "  ```bash",
        f"  python3 harness_cli.py run-fr-step --phase {phase} --fr-id {fr_id} --step TDD-RED \\",
        f"    --project .{srs_flag}",
        "  ```",
        f"  → Verify: `git log --oneline -1` shows `test(RED): failing test for {fr_id}`",
        "  → GitHub push: ✅ auto-done by run-fr-step",
        "",
        f"- [ ] **[ORCH-GREEN]** Dispatch TDD-GREEN sub-agent for {fr_id}:",
        "  ```bash",
        f"  python3 harness_cli.py run-fr-step --phase {phase} --fr-id {fr_id} --step TDD-GREEN \\",
        f"    --project .{srs_flag}",
        "  ```",
        f"  → Verify: `pytest tests/test_fr{num_str}.py -q` all pass",
        "  → GitHub push: ✅ auto-done by run-fr-step",
        "",
        f"- [ ] **[ORCH-IMPROVE]** Dispatch TDD-IMPROVE sub-agent for {fr_id}:",
        "  ```bash",
        f"  python3 harness_cli.py run-fr-step --phase {phase} --fr-id {fr_id} --step TDD-IMPROVE \\",
        "    --project .",
        "  ```",
        f"  → Verify: `pytest tests/test_fr{num_str}.py -q` still pass",
        "  → GitHub push: ✅ auto-done by run-fr-step",
        "",
        f"- [ ] **[ORCH-GATE1]** Dispatch GATE1 evaluator sub-agent for {fr_id}:",
        "  ```bash",
        f"  python3 harness_cli.py run-fr-step --phase {phase} --fr-id {fr_id} --step GATE1 \\",
        "    --project .",
        "  ```",
        f"  → Verify: `git log --oneline -1` shows `feat({fr_id}): Gate1 PASS`",
        "  → GitHub push: ✅ auto-done by run-fr-step",
        "  → GATE1 FAIL: auto-dispatches CODE-FIX sub-agent → retries (max 3 rounds)",
        "  → exit 2 = BLOCKED: human intervention required before continuing",
        f"  → Human fix → re-run `run-fr-step --step GATE1 --fr-id {fr_id}` → exit 0 required before continuing.",
        "",
        "- [ ] **[ORCH-POST]** After GATE1 PASS — orchestrator runs directly:",
        "  ```bash",
        f"  python3 harness_cli.py spec-coverage-check --project . --threshold 40.0 --fr-id {fr_id}",
        "  python3 scripts/generate_sab.py --project .",
        "  ```",
        "",
        f"> 💡 **Crash recovery**: `python3 harness_cli.py resume-fr-phase --phase {phase} --project .`",
        "> prints the next pending step (idempotent on re-run).",
        "",
    ]


def _fr_carryforward_steps(fr_id: str, phase: int) -> List[str]:
    """Gate 1 re-evaluation via GATE1-DELTA sub-agent for carry-forward FRs.

    GATE1-DELTA: git diff since last Gate 1 PASS → no changes: skip (idempotent);
    code changed: full GATE1 re-evaluation. GitHub push after sub-agent completes.
    """
    return [
        f"**Gate 1 Re-evaluation — {fr_id}** (carry-forward · sub-agent dispatch):",
        "- [ ] **[ORCH-GATE1-DELTA]** Dispatch GATE1-DELTA evaluator sub-agent:",
        "  ```bash",
        f"  python3 harness_cli.py run-fr-step --phase {phase} --fr-id {fr_id} \\",
        "    --step GATE1-DELTA --project .",
        "  ```",
        f"  → Code-change detection: git diff {fr_id} files since last Gate 1 PASS",
        "  → No changes → skip (idempotent — safe to re-run)",
        "  → Changes detected → full GATE1 re-evaluation (3 dims: linting/type_safety/test_coverage)",
        "  → GitHub push: ✅ auto-done by run-fr-step",
        "  → GATE1 FAIL: auto-dispatches CODE-FIX sub-agent → retries (max 3 rounds)",
        "  → exit 2 = BLOCKED: human intervention required before continuing",
        f"  → Human fix → re-run `run-fr-step --step GATE1-DELTA --fr-id {fr_id}` → exit 0 required before continuing.",
        "",
        "- [ ] **[ORCH-POST]** After GATE1-DELTA PASS — orchestrator runs directly:",
        "  ```bash",
        f"  python3 harness_cli.py spec-coverage-check --project . --threshold 40.0 --fr-id {fr_id}",
        "  python3 scripts/generate_sab.py --project .",
        "  ```",
        "",
    ]


def _sessions_spawn_deliverable() -> str:
    """sessions_spawn.log deliverable line — non-blocking debug trail (HR-10 enforcement removed)."""
    return (
        "- [x] `.methodology/sessions_spawn.log` — auto-populated by AgentSpawner "
        "(non-blocking debug trail)"
    )


def _phase_advance_step(phase: int, dynamic: bool = False) -> List[str]:
    """Instruction to advance to the next phase after all checkpoints PASS."""
    if phase >= 8:
        return [
            # Phase Truth (HR-11): applies to P3–P8 per SKILL.md §2
            *(["- [ ] **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase",
               "",
               ] if phase >= 3 else []),
            "### 🎉 Pipeline Complete",
            "",
            "- [ ] All 8 phases complete. Archive `.methodology/` for the audit trail.",
            "",
        ]
    next_phase = phase + 1
    next_names = {
        2: "Architecture Design", 3: "Implementation",
        4: "Testing", 5: "Verification & Delivery", 6: "Quality Assurance",
        7: "Risk Management", 8: "Configuration Management",
    }
    next_name = next_names.get(next_phase, f"Phase {next_phase}")
    # TDD thresholds: mirror _advance_prechecks in harness_cli.py
    _tdd_sc = 90.0 if phase >= 6 else (80.0 if phase >= 4 else 60.0)  # unified v2.6
    lines = [
        f"### Phase {phase} → Phase {next_phase}: {next_name}",
        "",
        *([] if dynamic else [
            f"- [ ] Generate Phase {next_phase} plan:",
            "  ```bash",
            f"  python3 harness_cli.py plan-phase --phase {next_phase} --project . \\",
            f"    --output .methodology/phase{next_phase}_plan.md",
            "  ```",
        ]),
        # Git tag step: SKILL.md §0.4 requires Gate 4 tag only (P6→P7 transition)
        *(["- [ ] **[GIT-TAG]** Push Gate 4 git tag (SKILL.md §0.4):",
           "  ```bash",
           "  SCORE=$(python3 -c \"import json; d=json.load(open('.sessi-work/gate4_result.json')); print(d.get('composite_score','XX'))\" 2>/dev/null || echo 'XX')",
           "  git tag -a \"harness-v4-$(date +%Y%m%d)-score${SCORE}\" -m \"Gate 4 PASS (score ${SCORE})\"",
           "  git push origin --tags",
           "  ```",
           ""] if phase == 6 else []),
        # Phase Truth (HR-11): gates cover P3/P4/P6; P5/P7 have no exit gate so add here
        *(["- [ ] **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase",
           "  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`",
           "  >   → identify which phase link or gate artifact failed",
           "  >   → fix artifacts → re-run `advance-phase`",
           "  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log",
           "",
           ] if phase >= 3 and phase not in _PHASE_EXIT_GATES else []),
        # TDD prechecks: advance-phase enforces pytest 100% cov + spec-coverage (exit 9/10)
        # P5→P6 warning: advance requires 80% but Gate 4 requires 90%
        *(["- [ ] **[D4-GAP WARNING]** Gate 4 (next phase) requires spec-coverage ≥ 90% but current advance threshold is 80%.",
           "  > Close this gap NOW to avoid a surprise Gate 4 D4 block.",
           "  > Check: `python3 harness_cli.py spec-coverage-check --project . --threshold 90.0`",
           "  > If below 90%: add missing test implementations before advancing to Phase 6.",
           "",
           ] if phase == 5 else []),
        *(["- [ ] **[TDD-PRECHECK]** Verify TDD checks pass — advance-phase enforces both:",
           "  - `pytest --tb=short -q --cov=03-development/src --cov-fail-under=100` (exit 9)",
           f"  - `python3 harness_cli.py spec-coverage-check --project . --threshold {_tdd_sc:.1f}` (exit 10, D4 unified v2.6)",
           "  > For genuinely untestable lines add: `# pragma: no cover` (requires justification comment).",
           "",
           ] if phase >= 3 else []),
        f"- [ ] Advance FSM to Phase {next_phase} (writes new HANDOVER.md + local commit):",
        "  ```bash",
        f"  python3 harness_cli.py advance-phase --completed {phase} --project .",
        "  ```",
        f"- [ ] Confirm `HANDOVER.md` reflects Phase {next_phase} entry (`P{next_phase}-entry` checkpoint, correct plan path)",
        f"- [ ] Open `phase{next_phase}_plan.md` and follow from the top.",
        f"- [ ] If session crashes during Phase {next_phase}: read `HANDOVER.md` or run `generate-next-plan`",
        "",
    ]
    return lines




def _dynamic_phase_context_block(phase: int, has_fr_template: bool = True) -> List[str]:
    """[PHASE-CONTEXT] block — load FR data at execution time (dynamic plans only)."""
    result = [
        "### 🔄 [PHASE-CONTEXT] — Load Before Starting",
        "",
        "```bash",
        f"python3 harness_cli.py load-context --phase {phase} --project . --json \\",
        f"  > .sessi-work/phase{phase}_ctx.json",
        "```",
        "> Outputs `fr_ids`, `fr_details`, `modules` from current project state.",
    ]
    if has_fr_template:
        result.append("> All `{FR-ID}` references in tasks below come from this file.")
    result.append("")
    return result


def _dynamic_fr_template_block(phase: int) -> List[str]:
    """FR task template for dynamic plans — each {FR-ID} is expanded at execution time."""
    use_carryforward = phase in (4, 5, 7, 8)
    if use_carryforward:
        fr_steps = [
            f"- [ ] **[ORCH-GATE1-DELTA]** `run-fr-step --phase {phase} --fr-id {{FR-ID}} --step GATE1-DELTA --project .`",
            "> Crash recovery: `resume-fr-phase` auto-detects code changes → switches to full TDD if needed.",
            f"> **Auto-skip**: if NO FR's code changed since its last Gate 1 PASS, `advance-phase --completed {phase}`",
            "> treats this entire DELTA loop as satisfied automatically — you may skip the per-FR steps.",
            "> Only FRs whose code actually changed need a re-evaluation.",
            ">",
            "> **GATE1-DELTA outcomes:**",
            "> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}",
            "> - CASE 2 FAIL:    Gate 1 FAIL → full TDD auto-triggered by crash recovery:",
            f">   `run-fr-step --phase {phase} --fr-id {{FR-ID}} --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1",
            "> - CASE 3 BLOCKED: 3 TDD rounds still failing → escalate to human.",
            ">   Provide: last Gate 1 output + pytest failure log.",
        ]
    else:
        fr_steps = [
            f"- [ ] **[ORCH-RED]**     `run-fr-step --phase {phase} --fr-id {{FR-ID}} --step TDD-RED --project . --srs 01-requirements/SRS.md`",
            f"- [ ] **[ORCH-GREEN]**   `run-fr-step --phase {phase} --fr-id {{FR-ID}} --step TDD-GREEN --project . --srs 01-requirements/SRS.md`",
            f"- [ ] **[ORCH-IMPROVE]** `run-fr-step --phase {phase} --fr-id {{FR-ID}} --step TDD-IMPROVE --project .`",
            f"- [ ] **[ORCH-GATE1]**   `run-fr-step --phase {phase} --fr-id {{FR-ID}} --step GATE1 --project .`",
            f"> Gate 1 thresholds: {_GATE_META[1][2]}",
            f"> Crash recovery: `resume-fr-phase --phase {phase} --project .`",
            ">",
            "> **Gate 1 outcomes:**",
            "> - CASE 1 PASS:    Gate 1 PASS → continue to next {FR-ID}",
            "> - CASE 2 FAIL:    Fix failing dims → re-run `run-fr-step --step GATE1`",
            ">   (linting: `ruff check . --fix`; coverage: add tests; type_safety: fix pyright errors)",
            "> - CASE 3 BLOCKED: 3 rounds still failing → escalate to human.",
            ">   Provide: Gate 1 output + failing dimension details.",
        ]
    return [
        "### FR Tasks — Expanded at Execution Time",
        "",
        "- [ ] **[ENV-CHECK]** Run ONCE before the FR loop — `GATE1`/`GATE1-DELTA` preflight"
        " requires `.sessi-work/env_check_result.json`:",
        "  ```bash",
        f"  python3 harness_cli.py run-env-check --phase {phase} --project .",
        "  # evaluate inline → write .sessi-work/env_check_result.json →",
        f"  python3 harness_cli.py finalize-env-check --phase {phase} --project .",
        "  ```",
        f"  > Without this, every `run-fr-step --step GATE1{'-DELTA' if use_carryforward else ''}` blocks on"
        " 'env_check_result.json not found'.",
        "",
        f"> Read `fr_ids` from `.sessi-work/phase{phase}_ctx.json`.",
        "> For each `{FR-ID}` in the list, execute the template below:",
        "",
        "---",
        "**{FR-ID} — {FR-TITLE from fr_details}**",
        "",
        *fr_steps,
        "",
        "---",
        "",
    ]


def _p3_milestone_push_steps(fr_ids: List[str], dynamic: bool = False) -> List[str]:
    """P3 milestone push instructions (PUSH ③ at ≥50% FRs, PUSH ④ pre-Gate2)."""
    return _milestone_push_steps(fr_ids, phase=3, pre_gate=2, push_prefixes=("③", "④"), dynamic=dynamic)


def _milestone_push_steps(fr_ids: List[str], phase: int,
                          pre_gate: int | None = None,
                          push_prefixes: tuple[str, str] = ("", ""),
                          header_note: str = "",
                          dynamic: bool = False) -> List[str]:
    """Phase milestone push instructions (mid + pre-gate push checkpoints).

    Args:
        pre_gate: Gate number for pre-gate milestone (e.g. 2 for P3 → "p3-pre-gate2").
            None = no pre-gate milestone generated.
        push_prefixes: (mid_label, pre_gate_label) — e.g. ("③", "④") for P3.
            Omit for phases without dedicated push numbers (P4+).
        header_note: Optional note appended to the section header (e.g. for P4 variant labels).
        dynamic: If True, output placeholder instructions when fr_ids is empty
            (execution-time user fills in FR IDs from load-context).
    """
    if not fr_ids:
        if not dynamic:
            return []
        total_str = "N"
        mid_str = "50%"
        mid_ids = "<comma-separated FR-IDs with Gate 1 PASS>"
        full_ids = "<comma-separated FR-IDs with Gate 1 PASS>"
        _visual = "<FR-01,FR-02,…>"
    else:
        total_int = len(fr_ids)
        mid_int = max(1, total_int // 2)
        total_str = str(total_int)
        mid_str = str(mid_int)
        mid_ids = ",".join(fr_ids[:mid_int])
        full_ids = ",".join(fr_ids)
        if len(fr_ids) > 5:
            _visual = ",".join(fr_ids[:5]) + f",…+{len(fr_ids) - 5}"
        else:
            _visual = full_ids

    _mid_prefix = f"PUSH {push_prefixes[0]} — " if push_prefixes[0] else ""
    _pre_prefix = f"PUSH {push_prefixes[1]} — " if push_prefixes[1] else ""
    _strategy_label = (f" (10-Push Strategy {push_prefixes[0]}{push_prefixes[1]})"
                       if push_prefixes[0] else "")
    _header_note = f" — {header_note}" if header_note else ""

    pre_gate_type = f"pre-gate{pre_gate}" if pre_gate else None
    result = [
        f"### P{phase} Milestone Pushes{_strategy_label}{_header_note}",
        "",
        "> Per-FR steps push automatically via `run-fr-step`. The two **milestone pushes** below",
        "> also write `HANDOVER.md` with phase/FR/status summary and push to origin.",
        f"> All FR IDs in this project: {_visual}",
        "",
        f"- [ ] **{_mid_prefix}P{phase}-mid** (trigger when ≥{mid_str}/{total_str} FRs have Gate 1 PASS):",
        "  ```bash",
        f"  python3 harness_cli.py push-milestone --type p{phase}-mid --project . \\",
        f"    --fr-done {mid_str} --fr-total {total_str} --fr-ids {mid_ids}",
        "  ```",
        f"  > `--fr-ids` lists the FRs with Gate 1 PASS so far. Replace `{mid_ids}` with actual.",
        "  > Writes HANDOVER.md + commits + pushes. Next session reads HANDOVER.md to resume.",
        "",
    ]
    if pre_gate_type:
        result += [
            f"- [ ] **{_pre_prefix}P{phase}-{pre_gate_type}** (trigger when all {total_str} FRs Gate 1 PASS, before Gate {pre_gate}):",
            "  ```bash",
            f"  python3 harness_cli.py push-milestone --type p{phase}-{pre_gate_type} --project . \\",
            f"    --fr-ids {full_ids}",
            "  ```",
            f"  > Last stable snapshot before Gate {pre_gate} evaluation. HANDOVER.md + push.",
            "",
        ]
    return result



def _gate4_prerequisites_block() -> List[str]:
    """Gate 4 result JSON required fields (A3) — inserted before _gate_exit_checkpoint(4)."""
    return [
        "### Gate 4 Result JSON — Required Fields",
        "",
        "> `finalize-gate --gate 4` validates A3 **before** scoring. Missing/insufficient → `[BLOCKED]`.",
        "",
        "- [ ] **[A3] `devil_advocate`** + **`devil_advocate_evidence`** — artifact-backed DA challenge for all Tier 3 dims:",
        "  ```json",
        '  "devil_advocate": {',
        '    "architecture": true, "readability": true, "error_handling": true,',
        '    "documentation": true, "performance": true',
        '  },',
        '  "devil_advocate_evidence": {',
        '    "architecture": {',
        '      "challenger_model": "claude",',
        '      "challenge": "<≥120 chars: the challenger persona\'s actual critique of the design/score>",',
        '      "response": "<≥120 chars: the defence / justification>"',
        '    }',
        '  }',
        "  ```",
        "  > A bare boolean is **not** accepted (A3 is artifact-backed): for each Tier 3 dim, dispatch a",
        "  > Claude sub-agent with a challenger persona, then record its `challenge` + `response` text.",
        "  > **Orchestrator Pattern** (architecture/error_handling score = 0 due to hub-and-spoke):",
        "  > complete the DA challenge AND add `\"da_waiver\": {\"architecture\": true}` to bypass the",
        "  > score threshold — the waiver also requires the `devil_advocate_evidence.architecture` artifact.",
        "  > See `harness/ssi/prompts/evaluate_dimension.md` §Orchestrator.",
        "",
        "  > _Optional (not a gate step)_ — **[A5]** `issue_registry`: for a useful audit",
        "  > trail, populate `.sessi-work/issue_registry.json` via `issue_tracker.py add`",
        "  > during G4b. Advisory only — agent-written, so it never blocks or verifies anything.",
        "",
    ]


def _gate_exit_checkpoint(gate_num: int, phase: int, checkpoint_n: int) -> List[str]:
    """Phase-exit gate evaluation steps (two-phase + push checkpoint)."""
    meta = _GATE_META[gate_num]
    crg_note = (
        "  (CRG recon triggered inside run-gate automatically — no separate action needed)"
        if gate_num in (3, 4) else ""
    )
    early_stop = [
        f"  **Early-stop cases after G{gate_num}c:**",
        f"  - CASE 1 PASS:     score ≥ score_gate AND critical==0 → `quality_complete=True` → G{gate_num}d",
        f"  - CASE 2 CONTINUE: score ≥ score_gate BUT issues remain → fix → repeat G{gate_num}a",
        "  - CASE 3 PLATEAU:  3 consecutive rounds, no new issues → `deferred_fixes.md` → proceed to push",
        "  - CASE 4 BLOCKED:  max_rounds exhausted, not PASS → `GateBlockedError` → escalate to human",
        f"    > Human fix → re-run `run-gate --gate {gate_num} → finalize-gate --gate {gate_num}` → CASE 1 PASS required before continuing.",
    ]
    phase_truth_step = (
        [
            "- [ ] **[PHASE-TRUTH]** Phase Truth ≥ 90% (HR-11) — verified by advance-phase",
            "  > **FAIL** → check `phase_truth_verifier` output in `.sessi-work/`",
            "  >   → identify which phase link or gate artifact failed",
            "  >   → fix artifacts → re-run `advance-phase`",
            "  >   → If 3 consecutive failures: escalate to human with `phase_truth_verifier` log",
            "",
        ] if phase >= 3 else [
            f"- [ ] **[PHASE-TRUTH]** Phase Truth — N/A (P{phase} prerequisite only)",
            "",
        ]
    )

    g4ef_steps: List[str] = []
    if gate_num == 4:
        g4ef_steps = [
            "- [ ] **G4e** Generate Release Notes:",
            "  Create `RELEASE_NOTES.md` at project root summarizing changes since Gate 3.",
            "  Include: version, date, FR list, Gate 4 composite score, known limitations.",
            "  Reference: `06-quality/QUALITY_REPORT.md` (auto-generated by G4c finalize-gate).",
            "",
            "- [ ] **G4f** Generate Final Sign-Off:",
            "  Create `FINAL_SIGN_OFF.md` at project root.",
            "  Include: project name, completion date, Gate 4 composite score, sign-off statement.",
            "  Must reference `BASELINE.md` and `VERIFICATION_REPORT.md` (verification provenance).",
            "",
            "- [ ] **G4g** Agent B Peer Review (HR-01):",
            "  Agent B (ARCHITECT) reviews `06-quality/QUALITY_REPORT.md` and `RELEASE_NOTES.md`.",
            "  Confirm all FRs are merged and Gate 4 score ≥ 85.",
            "",
        ]

    return [
        "",
        f"### 🔒 CHECKPOINT-GATE-{gate_num}: Phase {phase} Exit",
        f"> {meta[2]}",
        "",
        f"- [ ] **G{gate_num}a** Prepare Gate {gate_num}:",
        "  ```bash",
        f"  python3 harness_cli.py run-gate --gate {gate_num} --phase {phase} --project .",
        "  ```",
        "  Read the evaluation prompt printed above.",
        *([crg_note] if crg_note else []),
        "",
        f"- [ ] **G{gate_num}b** Evaluate all Gate {gate_num} dimensions inline:",
        "  - Follow `harness/ssi/prompts/evaluate_dimension.md`",
        f"  - Write result to `.sessi-work/gate{gate_num}_result.json`",
        *(["  - Failing dim: fix code → re-evaluate → re-score"] if gate_num > 1 else []),
        *(["  > Failing dims: fix the root cause in code, then re-evaluate → re-score.",
           "  > (Auto-fix engine is NOT wired — fixes require manual code changes or targeted tools.)",
           ] if gate_num <= 4 else []),
        *(["  > **architecture** is framework-owned: the harness runs an independent CRG build itself",
           "  > (`harness/crg_independent.py`) and overrides any agent-recorded score with",
           "  > `community_cohesion`. error_handling is tool-scored (`ast-error-handling`), not CRG.",
           "  > If architecture = 0 due to Orchestrator/hub-and-spoke pattern: complete DA challenge (A3 above)",
           "  > and set `da_waiver` in gate4_result.json to bypass the threshold.",
           "  > See `harness/ssi/prompts/evaluate_dimension.md` §Orchestrator Pattern False Positive.",
           "  > **traceability** is also framework-owned: the harness calls `compute_trace_dimension()`",
           "  > inside `finalize-gate` and injects the score automatically. Do NOT report a traceability",
           "  > score in gate_result.json. If the gate is blocked by traceability, fix gaps then run:",
           "  > `python3 harness_cli.py build-trace-attestation --project . --write`",
           "  > `git add .methodology/trace/attestation.json && git commit -m 'trace: regen attestation'`",
           ] if gate_num in (3, 4) else
          ["  > **traceability** is framework-owned: the harness calls `compute_trace_dimension()`",
           "  > inside `finalize-gate` and injects the score automatically. Do NOT report a traceability",
           "  > score in gate_result.json. If the gate is blocked by traceability, fix gaps then run:",
           "  > `python3 harness_cli.py build-trace-attestation --project . --write`",
           "  > `git add .methodology/trace/attestation.json && git commit -m 'trace: regen attestation'`",
           ] if gate_num == 2 else []),
        "",
        f"- [ ] **G{gate_num}c** Finalize Gate {gate_num}:",
        "  ```bash",
        f"  python3 harness_cli.py finalize-gate --gate {gate_num} --phase {phase} --project .",
        "  ```",
        *(["  > **PUSH ⑧ in the 10-Push Strategy**: `finalize-gate --gate 4` writes HANDOVER.md + commits + pushes."] if gate_num == 4 else []),
        f"- [ ] **[D4]** D4 spec-coverage-check — unified v2.6 (Gate {gate_num} threshold {_SPEC_COVERAGE_THRESHOLDS[gate_num]:.0f}%):",
        "  ```bash",
        f"  python3 harness_cli.py spec-coverage-check --project . --threshold {_SPEC_COVERAGE_THRESHOLDS[gate_num]}",
        "  ```",
        "  FAIL → fix missing test implementations → re-run until coverage meets threshold",
        "",
        *early_stop,
        "",
        f"- [ ] **G{gate_num}d** ✅ Verify checkpoint saved (finalize-gate above already pushed + wrote HANDOVER.md):",
        "  ```bash",
        "  # Confirm HANDOVER.md exists at project root (written by finalize-gate → commit_and_push_gate)",
        "  ls -la HANDOVER.md",
        "  git log --oneline -1",
        "  ```",
        f"  > `finalize-gate --gate {gate_num}` (G{gate_num}c) calls `commit_and_push_gate()` which writes",
        "  > `HANDOVER.md` **before** committing + pushing. No separate push needed here.",
        "  > If HANDOVER.md is missing, re-run `finalize-gate` (do **not** raw-push).",
        "",
    ] + g4ef_steps + phase_truth_step


def _checkpoint_index(fr_ids: List[str], phase: int) -> List[str]:
    """Generate a checkpoint index header for the plan (P3-P8)."""
    lines = [
        f"> **Crash Recovery**: `python3 harness_cli.py resume-fr-phase --phase {phase} --project .`",
        "> prints the next pending step. Each `run-fr-step` auto-pushes to GitHub on completion.",
        "> Per-FR TDD-RED/GREEN/IMPROVE/GATE1 each push immediately (idempotent on re-run).",
        "> At milestones, `HANDOVER.md` is written with phase/FR/status summary.",
        "",
        "> **Checkpoint Index**:",
    ]
    cp = 1
    if phase == 4:
        lines.append("> - CHECKPOINT-0: TEST_PLAN.md (generate before per-FR testing starts)")
    if phase in _PHASE_GATE1_PHASES:
        for fr_id in fr_ids:
            lines.append(f"> - CHECKPOINT-{cp}: Gate 1 / {fr_id} *(auto-push via run-fr-step)*")
            cp += 1
    if phase == 3:
        lines.append("> - MILESTONE: P3-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**")
        lines.append("> - MILESTONE: P3-pre-gate2 push (all FRs done) → **HANDOVER.md**")
    if phase == 4:
        lines.append("> - MILESTONE: P4-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**")
        lines.append("> - MILESTONE: P4-pre-gate3 push (all FRs done, before Gate 3) → **HANDOVER.md**")
    if phase == 5:
        lines.append("> - MILESTONE: P5-baseline push (BASELINE.md generated) → **HANDOVER.md**")
    if phase == 7:
        lines.append("> - MILESTONE: P7 exit push (risk register complete) → **HANDOVER.md**")
    if phase == 8:
        lines.append("> - MILESTONE: P8 exit push (config records complete) → **HANDOVER.md**")
    if phase in _PHASE_EXIT_GATES:
        gate_num = _PHASE_EXIT_GATES[phase]
        if phase == 6:
            lines.append("> - CHECKPOINT-GATE-4: Gate 4 (Full Project — 15 dims) + Agent B peer review → **push + HANDOVER.md**")
        else:
            lines.append(f"> - CHECKPOINT-GATE-{gate_num}: Gate {gate_num} (Phase {phase} Exit) → **push + HANDOVER.md**")
    lines.append("")
    return lines


def _load_manifest_fr_ids(repo_path: Path) -> List[str]:
    """Try to read fr_ids from quality_manifest.json. Falls back to empty list."""
    import json
    manifest_path = repo_path / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8")).get("fr_ids", [])
        except Exception:  # pylint: disable=broad-exception-caught
            import logging
            logging.getLogger(__name__).warning(
                "Failed to parse quality_manifest.json for FR IDs", exc_info=True
            )
    return []


# ============================================================================
# Phase Task Generators
# ============================================================================

def generate_phase1_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 1 detailed tasks (Requirements Specification).

    Exit gate = Agent B peer review (NOT harness run-gate).
    A/B is serial per-deliverable: SRS → SPEC_TRACKING → TRACEABILITY.
    Each deliverable has its own A/B loop; REJECT only backtracks one step.
    """
    _ = repo_path  # reserved for future use (e.g. reading .methodology/state.json)
    lines = []
    lines.append("## Phase 1 Tasks: Requirements Specification")
    lines.append("")
    lines.append("### Phase 1 Overview")
    lines.append("Phase 1 is the project starting point. Define complete SRS.")
    lines.append("**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).")
    lines.append("")

    lines.append("> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.")
    lines.append("> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.")
    lines.append("")

    # P1 has exactly one checkpoint: human sign-off at phase end
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):")
    lines.append("> - CHECKPOINT-PEER-REVIEW: Agent B Peer Review (Phase 1 Exit) → `push-checkpoint --phase 1`")
    lines.append("")

    lines.extend([
        "### Phase 1 Precondition",
        "",
        "- [ ] **[PROJECT-BRIEF]** Prepare `PROJECT_BRIEF.md` at project root **before starting Phase 1**:",
        "  - Project domain, stakeholders, business goals (1–2 pages)",
        "  - Key constraints (technical, regulatory, budget, timeline)",
        "  - This file is **Agent B's primary context** for all P1 reviews (embedded as DOC 1 in each B-1 prompt)",
        "  - Source: project owner / product manager supplies this before Phase 1 begins",
        "  - Not a P1 deliverable — it is the seed input that drives requirements authoring",
        "",
    ])

    lines.extend(_preflight_steps(1))

    # Load execution-time context BEFORE starting any sub-task (dynamic mode only).
    if dynamic:
        lines.extend(_dynamic_phase_context_block(1, has_fr_template=False))

    # Task decomposition: 3 deliverables with sequential dependencies
    lines.extend(_decomposition_section(1))

    # Serial per-deliverable A/B blocks
    deliverables = _PHASE_DELIVERABLE_DEPS.get(1, [])
    total = len(deliverables)
    label_to_sub_n = {d["label"]: i for i, d in enumerate(deliverables, 1)}
    lines.append("### Requirements Authoring (Serial A/B per Deliverable)")
    lines.append("")
    for i, d in enumerate(deliverables, 1):
        lines.extend(_deliverable_ab_block(1, d, i, total, label_to_sub_n))

    if not dynamic:
        # FR/NFR summary (informational — parsed from already-APPROVED SRS.md if exists)
        frs = parse_srs_fr_sections(srs_path)
        nfrs = parse_srs_nfr_sections(srs_path)

        if frs:
            lines.append("### FR Requirements ({} total)".format(len(frs)))
            lines.append("")
            for fr in frs:
                title = fr['title']
                fr_prefix = f"{fr['fr']}: "
                if title.startswith(fr_prefix):
                    title = title[len(fr_prefix):]
                lines.append(f"#### {fr['fr']}: {title}")
                lines.append(f"**Task**: {fr['desc']}")
                if fr['requirements']:
                    lines.append("**Requirements**:")
                    for req in fr['requirements'][:5]:
                        lines.append(f"- {req}")
                lines.append("")

        if nfrs:
            lines.append("### NFR Non-Functional Requirements ({} total)".format(len(nfrs)))
            lines.append("")
            for nfr in nfrs:
                _nfr_title = nfr['title'].replace(f"{nfr['nfr']}: ", '', 1)
                lines.append(f"#### {nfr['nfr']}: {_nfr_title}")
                lines.append(f"**Requirement**: {nfr['details'][:200]}")
                lines.append("")

    lines.append("### Phase 1 Deliverables")
    lines.append("- [ ] `SRS.md` - Software Requirements Specification (FRs + NFRs)")
    lines.append("- [ ] `SPEC_TRACKING.md` - Spec tracking matrix")
    lines.append("- [ ] `TRACEABILITY_MATRIX.md` - Requirements traceability matrix")
    lines.append("- [ ] `TEST_INVENTORY.yaml` - Test inventory (P1 naming authority — feeds TEST_SPEC.md)")
    lines.append(_sessions_spawn_deliverable())
    lines.append("")

    lines.extend(_review_checkpoint(1, checkpoint_n=1))
    lines.extend(_phase_advance_step(1, dynamic=dynamic))
    return lines


def generate_phase2_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 2 detailed tasks (Architecture Design).

    Entry = P1 human APPROVE.  Exit gate = Agent B peer review of SAD + ADR (NOT harness run-gate).
    """
    lines = []
    lines.append("## Phase 2 Tasks: Architecture Design")
    lines.append("")
    lines.append("### Phase 2 Overview")
    lines.append("Phase 2 designs the system architecture based on SRS, producing SAD and ADR.")
    lines.append("**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).")
    lines.append("")
    lines.append("> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.")
    lines.append("> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.")
    lines.append("")

    # P2 has exactly one checkpoint: human sign-off at phase end
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):")
    lines.append("> - CHECKPOINT-PEER-REVIEW: Agent B Peer Review (Phase 2 Exit) → `push-checkpoint --phase 2`")
    lines.append("")

    lines.extend(_entry_gate_check(2))  # confirm P1 human APPROVE
    lines.extend(_preflight_steps(2))

    # Load execution-time context BEFORE starting any sub-task (dynamic mode only).
    if dynamic:
        lines.extend(_dynamic_phase_context_block(2, has_fr_template=False))

    # Task decomposition: 3 deliverables with sequential dependencies
    lines.extend(_decomposition_section(2))

    # Serial per-deliverable A/B blocks
    deliverables = _PHASE_DELIVERABLE_DEPS.get(2, [])
    total = len(deliverables)
    label_to_sub_n = {d["label"]: i for i, d in enumerate(deliverables, 1)}
    lines.append("### Architecture Design (Serial A/B per Deliverable)")
    lines.append("")
    for i, d in enumerate(deliverables, 1):
        lines.extend(_deliverable_ab_block(2, d, i, total, label_to_sub_n))

    if not dynamic:
        frs = parse_srs_fr_sections(srs_path)
        modules = parse_sad_modules(repo_path)

        if frs:
            lines.append("### FR Architecture Mapping ({} total)".format(len(frs)))
            lines.append("")
            for fr in frs:
                title = fr['title']
                fr_prefix = f"{fr['fr']}: "
                if title.startswith(fr_prefix):
                    title = title[len(fr_prefix):]
                lines.append(f"#### {fr['fr']}: {title}")
                lines.append(f"**Requirement**: {fr['desc']}")

                mod = modules.get(fr['fr'], {})
                if mod:
                    lines.append("**Module Mapping**:")
                    lines.append(f"- Module: `{mod.get('module', 'N/A')}`")
                    lines.append(f"- File:   `{mod.get('file', 'N/A')}`")
                lines.append("")

    lines.extend([
        "### SAB Generation (Machine-Readable Architecture Baseline)",
        "",
        "- [ ] **[SAB]** Generate `.methodology/SAB.json` from SAD.md §6 SAB block:",
        "  ```bash",
        "  python3 scripts/generate_sab.py --project .",
        "  ```",
        "  - SAB.json contains: layers, modules, allowed_dependencies, quality_targets",
        "  - Used by: drift detector (M2), gate architecture dimension, constitution check",
        "  - Also embedded inline in `quality_manifest.json` via `harness_bridge`",
        "",
    ])
    lines.append("### Phase 2 Deliverables")
    lines.append("- [ ] `SAD.md` — Software Architecture Document (every FR has module mapping)")
    lines.append("- [ ] `ADR.md` — Architecture Decision Records (tech stack, patterns, interfaces)")
    lines.append("- [ ] `TEST_SPEC.md` — Test specification catalog (named test cases from SRS, single source of truth — D4 unified check)")
    lines.append("- [ ] `.methodology/quality_manifest.json` — Quality manifest (FR list + SAB data)")
    lines.append("- [ ] `.methodology/SAB.json` — Machine-readable architecture baseline")
    lines.append(_sessions_spawn_deliverable())
    lines.append("")

    lines.extend(_review_checkpoint(2, checkpoint_n=1))
    lines.extend(_phase_advance_step(2, dynamic=dynamic))
    return lines


def generate_phase3_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 3 detailed tasks (Implementation + Gate 1 per-FR + Gate 2 exit)"""
    lines = []
    lines.append("## Phase 3 Tasks: Implementation")
    lines.append("")
    lines.append("### Phase 3 Overview")
    lines.append("Phase 3 implements all FR modules according to SAD, including unit tests.")
    lines.append("Each FR ends with a Gate 1 quality evaluation (CHECKPOINT). Phase exits via Gate 2.")
    lines.append("")

    if dynamic:
        fr_ids: List[str] = []
        checkpoint_n = 1
        lines.extend(_checkpoint_index(fr_ids, phase=3))
        lines.extend(_entry_gate_check(3))
        lines.extend(_preflight_steps(3))
        lines.extend(_dynamic_phase_context_block(3))
        lines.extend(_dynamic_fr_template_block(3))
        lines.extend(_p3_milestone_push_steps(fr_ids, dynamic=True))
    else:
        frs = parse_srs_fr_sections(srs_path)
        modules = parse_sad_modules(repo_path)

        # Try manifest for definitive FR list
        manifest_fr_ids = _load_manifest_fr_ids(repo_path)
        fr_ids = manifest_fr_ids if manifest_fr_ids else [fr['fr'] for fr in frs]

        lines.extend(_checkpoint_index(fr_ids, phase=3))
        lines.extend(_entry_gate_check(3))
        lines.extend(_preflight_steps(3))

        if frs and fr_ids:
            srs_fr_map = {fr['fr']: fr for fr in frs}
            srs_fr_set = set(srs_fr_map.keys())
            carry_forward = [fid for fid in fr_ids if fid not in srs_fr_set]

            if carry_forward:
                lines.append("### FR Implementation Tasks ({} total: {} new + {} carry-forward)".format(
                    len(fr_ids), len(frs), len(carry_forward)))
                lines.append("")
                lines.append(f"> **Carry-forward from Phase 1**: {', '.join(carry_forward)} — "
                             "already implemented; Gate 1 re-evaluation only.")
                lines.append("")
            else:
                lines.append("### FR Implementation Tasks ({} total)".format(len(frs)))
                lines.append("")

            checkpoint_n = 1
            for fr_id in fr_ids:
                if fr_id in srs_fr_map:
                    fr = srs_fr_map[fr_id]
                    title = fr['title']
                    fr_prefix = f"{fr['fr']}: "
                    if title.startswith(fr_prefix):
                        title = title[len(fr_prefix):]
                    lines.append(f"#### {fr['fr']}: {title}")
                    lines.append(f"**Task**: {fr['desc']}")

                    if fr['requirements']:
                        lines.append("**SRS Requirements**:")
                        for req in fr['requirements'][:5]:
                            lines.append(f"- {req}")

                    if fr['test_cases']:
                        lines.append("**Test Cases**:")
                        for inp, out in fr['test_cases']:
                            lines.append(f"- Input [{inp}] -> Output [{out}]")

                    mod = modules.get(fr['fr'], {})
                    if mod:
                        lines.append("**SAD Mapping**:")
                        lines.append(f"- Module: `{mod.get('module', 'N/A')}`")
                        lines.append(f"- File:   `{mod.get('file', 'N/A')}`")

                    lines.append("**Forbidden**:")
                    lines.append("- app/infrastructure/ (deprecated)")
                    lines.append("- @covers: L1 Error")
                    lines.append("- @type: edge")
                    lines.append("")

                    lines.extend(_fr_dev_steps(fr['fr'], phase=3))
                else:
                    lines.append(f"#### {fr_id}: Re-evaluation (carry-forward)")
                    lines.append("> Already implemented in Phase 1. Gate 1 re-run to verify no regressions.")
                    lines.append("")
                    lines.extend(_fr_carryforward_steps(fr_id, phase=3))

                # Gate 1 is handled inside the sub-agent dispatch (run-fr-step).
                # _gate1_checkpoint() removed — no duplicate inline G1a/G1b/G1c/G1d.
                checkpoint_n += 1

        elif fr_ids:
            lines.append("### FR Implementation Tasks ({} total)".format(len(fr_ids)))
            lines.append("")
            checkpoint_n = 1
            for fr_id in fr_ids:
                lines.append(f"#### {fr_id}: [See SRS.md and SAD.md for implementation details]")
                lines.append("")
                lines.extend(_fr_dev_steps(fr_id, phase=3))
                # Gate 1 handled inside run-fr-step sub-agent dispatch.
                checkpoint_n += 1

        else:
            lines.append("### ⚠️  FR Implementation Tasks — NONE FOUND")
            lines.append("")
            lines.append("> **WARNING**: `parse_srs_fr_sections()` returned zero FRs and no")
            lines.append("> `quality_manifest.json` FR list was found. The plan has **no per-FR")
            lines.append("> task blocks**.  Verify SRS.md format: use `### FR-01: Title` sections")
            lines.append("> or `| FR-01 | description |` table rows.")
            lines.append("")
            lines.append("To fix: update SRS.md format and re-run `plan-phase --phase 3`.")
            lines.append("")
            checkpoint_n = 1

        # NFR summary — informational, shows which NFRs each FR implements
        nfrs = parse_srs_nfr_sections(srs_path)
        if nfrs:
            # Build NFR→FRs reverse map.  Primary source: §2 cross-reference table
            # (parse_srs_fr_nfr_xref).  Fallback: search raw FR description text for
            # NFR IDs (works when SRS embeds NFR refs inside FR sections directly).
            _fr_nfr_xref = parse_srs_fr_nfr_xref(srs_path)
            _nfr_to_frs: Dict[str, List[str]] = {}
            for _fr_id, _nfr_ids in _fr_nfr_xref.items():
                for _nfr_id in _nfr_ids:
                    _nfr_to_frs.setdefault(_nfr_id, []).append(_fr_id)

            lines.append("### NFR Coverage ({} total)".format(len(nfrs)))
            lines.append("")
            lines.append("> NFRs are implemented **within FRs** — each FR satisfies one or more NFRs.")
            lines.append("> Verify NFR compliance via Gate 2/3 tool-scored dimensions, not separate tasks.")
            lines.append("")
            lines.append("| NFR | Type | FRs Implementing |")
            lines.append("|-----|------|-----------------|")
            for nfr in nfrs:
                nfr_id = nfr['nfr']
                nfr_type = nfr.get('title', '').replace(f'{nfr_id}: ', '')
                # Primary: cross-reference table lookup
                _ref_frs = _nfr_to_frs.get(nfr_id, [])
                # Fallback: grep NFR ID from FR raw_details text
                if not _ref_frs:
                    _ref_frs = [
                        fr['fr'] for fr in frs
                        if nfr_id.lower() in fr.get('raw_details', '').lower()
                    ] if frs else []
                fr_list = ', '.join(_ref_frs)
                if not fr_list:
                    fr_list = '—'
                lines.append(f"| {nfr_id} | {nfr_type[:30]} | {fr_list} |")
            lines.append("")
            if not _fr_nfr_xref:
                lines.append("> ⚠️ **NFR→FR mapping not found** — `—` entries above indicate no `NFR Association`")
                lines.append("> column was detected in SRS.md FR tables. To enable auto-mapping, add an")
                lines.append("> `NFR Association` column to each FR row in `01-requirements/SRS.md §2`.")
                lines.append("")
            lines.append("**Gate 2 NFR dimensions** (tool-scored, see Gate 2 config):")
            lines.append("- `security` (bandit), `secrets_scanning` (gitleaks), `mutation_testing` (mutmut 2.x — `pip install 'mutmut<3'`)")
            lines.append("- `integration_coverage` (pytest), `test_assertion_quality` (pytest)")
            lines.append("")
        else:
            # NFRs couldn't be parsed — remind the user to check manually
            lines.append("### NFR Coverage")
            lines.append("")
            lines.append("> ⚠️  NFR sections could not be parsed from SRS.md.")
            lines.append("> Verify NFR compliance manually against `01-requirements/SRS.md` §3.")
            lines.append("")

        lines.extend(_p3_milestone_push_steps(fr_ids))

    lines.extend(_gate_exit_checkpoint(gate_num=2, phase=3, checkpoint_n=checkpoint_n))

    lines.append("### Phase 3 Deliverables")
    lines.append("- [ ] `03-development/src/` - All FR modules implemented")
    lines.append("- [ ] `tests/` - Unit tests (≥80% coverage per FR)")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("- [ ] Gate 2 PASS (phase exit, composite ≥ 75)")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(3, dynamic=dynamic))
    return lines


def generate_phase4_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 4 detailed tasks (Testing + Gate 1 per-FR + Gate 3 exit)"""
    lines = []
    lines.append("## Phase 4 Tasks: Test Planning & Execution")
    lines.append("")
    lines.append("### Phase 4 Overview")
    lines.append("Phase 4 formulates and executes a complete test plan based on Phase 3 code.")
    lines.append("Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). Phase exits via Gate 3 (15 dims).")
    lines.append("")

    if dynamic:
        fr_ids: List[str] = []
        checkpoint_n = 1
        lines.extend(_checkpoint_index(fr_ids, phase=4))
        lines.extend(_entry_gate_check(4))
        lines.extend(_preflight_steps(4))
        lines.extend(_dynamic_phase_context_block(4))
        # ── CHECKPOINT-0: Generate TEST_PLAN.md before any FR testing ─────────
        lines.append("### CHECKPOINT-0: Generate TEST_PLAN.md")
        lines.append("")
        lines.append("> Generate `04-testing/TEST_PLAN.md` from SRS.md FR acceptance criteria.")
        lines.append("> This step runs once before per-FR test execution.")
        lines.append("")
        lines.append("**Generate TEST_PLAN.md** (orchestrator runs directly — not a sub-agent dispatch):")
        lines.append("- [ ] Read SRS.md FR acceptance criteria → write TEST_PLAN.md with per-FR test cases")
        lines.append("  - For each FR: test case ID, description, input, expected output, priority")
        lines.append("  - Include positive, negative, boundary, and edge case categories")
        lines.append("  - Output: `04-testing/TEST_PLAN.md`")
        lines.append("- [ ] Verify TEST_PLAN.md covers all FRs from manifest/quality_manifest.json")
        lines.append("- [ ] **[TP-DONE]** TEST_PLAN.md written: all FRs have ≥1 test case, NFRs addressed")
        lines.append("")
        lines.extend(_dynamic_fr_template_block(4))
        lines.extend(_milestone_push_steps(fr_ids, phase=4, pre_gate=3,
                                           push_prefixes=("⑤", "⑥"),
                                           dynamic=True))
    else:
        frs = parse_srs_fr_sections(srs_path)
        test_plans = parse_test_plan(repo_path)
        manifest_fr_ids = _load_manifest_fr_ids(repo_path)
        fr_ids = manifest_fr_ids if manifest_fr_ids else [fr['fr'] for fr in frs]

        lines.extend(_checkpoint_index(fr_ids, phase=4))
        lines.extend(_entry_gate_check(4))
        lines.extend(_preflight_steps(4))

        # ── CHECKPOINT-0: Generate TEST_PLAN.md before any FR testing ─────────
        lines.append("### CHECKPOINT-0: Generate TEST_PLAN.md")
        lines.append("")
        lines.append("> Generate `04-testing/TEST_PLAN.md` from SRS.md FR acceptance criteria.")
        lines.append("> This step runs once before per-FR test execution.")
        lines.append("")
        lines.append("**Generate TEST_PLAN.md** (orchestrator runs directly — not a sub-agent dispatch):")
        lines.append("- [ ] Read SRS.md FR acceptance criteria → write TEST_PLAN.md with per-FR test cases")
        lines.append("  - For each FR: test case ID, description, input, expected output, priority")
        lines.append("  - Include positive, negative, boundary, and edge case categories")
        lines.append("  - Output: `04-testing/TEST_PLAN.md`")
        lines.append("- [ ] Verify TEST_PLAN.md covers all FRs from manifest/quality_manifest.json")
        lines.append("- [ ] **[TP-DONE]** TEST_PLAN.md written: all FRs have ≥1 test case, NFRs addressed")
        lines.append("")

        checkpoint_n = 1
        if test_plans:
            # Test plan exists — show items; Gate 1 still runs per FR from manifest
            lines.append("### Test Plan Items ({} total from TEST_PLAN.md)".format(len(test_plans)))
            lines.append("")
            for tp in test_plans:
                lines.append(f"#### {tp['title']}")
                lines.append(f"**Content**: {tp['details'][:200]}")
                lines.append("")
            if fr_ids:
                lines.append("### FR Gate 1 Evaluations ({} FRs from manifest)".format(len(fr_ids)))
                lines.append("> **Cross-reference**: Agent A's test scope for each FR = TEST_PLAN.md items above.")
                lines.append("> Match TEST_PLAN.md items to this FR's module before writing/executing tests.")
                lines.append("")
                for fr_id in fr_ids:
                    lines.append(f"#### {fr_id}: Test Execution")
                    lines.append("")
                    lines.extend(_fr_dev_steps(fr_id, phase=4))
                    # Gate 1 handled inside run-fr-step sub-agent dispatch.
                    checkpoint_n += 1
        elif frs and fr_ids:
            srs_fr_map = {fr['fr']: fr for fr in frs}
            srs_fr_set = set(srs_fr_map.keys())
            carry_forward = [fid for fid in fr_ids if fid not in srs_fr_set]

            if carry_forward:
                lines.append("### FR Test Coverage ({} total: {} new + {} carry-forward)".format(
                    len(fr_ids), len(frs), len(carry_forward)))
                lines.append("")
                lines.append(f"> **Carry-forward from Phase 1**: {', '.join(carry_forward)} — "
                             "already implemented; Gate 1 re-evaluation only.")
                lines.append("")
            else:
                lines.append("### FR Test Coverage")
                lines.append("")

            checkpoint_n = 1
            for fr_id in fr_ids:
                if fr_id in srs_fr_map:
                    fr = srs_fr_map[fr_id]
                    title = fr['title']
                    fr_prefix = f"{fr['fr']}: "
                    if title.startswith(fr_prefix):
                        title = title[len(fr_prefix):]
                    lines.append(f"#### {fr['fr']}: {title}")
                    lines.append(f"**Test Target**: Verify {fr['desc']}")
                    if fr['test_cases']:
                        lines.append("**Test Cases**:")
                        for inp, out in fr['test_cases']:
                            lines.append(f"- Input [{inp}] -> Output [{out}]")
                    lines.append("")
                    lines.extend(_fr_dev_steps(fr['fr'], phase=4))
                else:
                    lines.append(f"#### {fr_id}: Re-evaluation (carry-forward)")
                    lines.append("> Already implemented. Gate 1 re-run to verify no regressions.")
                    lines.append("")
                    lines.extend(_fr_carryforward_steps(fr_id, phase=4))

                # Gate 1 handled inside run-fr-step sub-agent dispatch.
                checkpoint_n += 1
        elif fr_ids:
            lines.append("### FR Test Coverage ({} FRs)".format(len(fr_ids)))
            lines.append("")
            for fr_id in fr_ids:
                lines.append(f"#### {fr_id}: [See SRS.md for test targets]")
                lines.append("")
                lines.extend(_fr_dev_steps(fr_id, phase=4))
                # Gate 1 handled inside run-fr-step sub-agent dispatch.
                checkpoint_n += 1
        else:
            lines.append("### ⚠️  FR Test Coverage — NONE FOUND")
            lines.append("")
            lines.append("> **WARNING**: No FR sections parsed and no manifest FR list found.")
            lines.append("> Verify SRS.md format or re-run `plan-phase --phase 4` after")
            lines.append("> quality_manifest.json is generated.")
            lines.append("")
            checkpoint_n = 1

    lines.extend([
        "### TEST_RESULTS.md Summary",
        "",
        "- [ ] **[TEST-RESULTS-SUMMARY]** Finalize `04-testing/TEST_RESULTS.md` before milestone push:",
        "  - Summarise test execution: test cases run, pass/fail outcome, any deferred issues",
        "  - Real test execution is enforced by advance-phase TDD-PRECHECK "
        "(`pytest --cov-fail-under=100`), not by string-matching this document",
        "",
        "### COVERAGE_REPORT.md — Coverage Summary",
        "",
        "- [ ] **[COVERAGE-REPORT]** Generate `04-testing/COVERAGE_REPORT.md`:",
        "  ```bash",
        "  pytest --cov=03-development/src --cov-report=term-missing -q \\",
        "    | tee 04-testing/coverage_raw.txt",
        "  python3 -m coverage report --format=total  # → overall %",
        "  ```",
        "  Write `04-testing/COVERAGE_REPORT.md` including:",
        "  - Overall coverage % (must be ≥80% for Gate 3)",
        "  - Per-module breakdown (from term-missing output)",
        "  - Uncovered lines (if any)",
        "  > cross_artifact.py validates this file's numbers against live `pytest --cov` at Gate 3.",
        "  > Fabricated numbers will be caught by the cross-artifact check.",
        "",
    ])

    if not dynamic:
        lines.extend(_milestone_push_steps(fr_ids, phase=4, pre_gate=3,
                                           push_prefixes=("⑤", "⑥")))

    lines.extend(_gate_exit_checkpoint(gate_num=3, phase=4, checkpoint_n=checkpoint_n))

    lines.append("### Phase 4 Deliverables")
    lines.append("- [ ] `04-testing/TEST_PLAN.md` - Test plan")
    lines.append("- [ ] `04-testing/TEST_RESULTS.md` - Test results (test execution summary)")
    lines.append("- [ ] `04-testing/COVERAGE_REPORT.md` - Coverage report")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("- [ ] Gate 3 PASS (phase exit, composite ≥ 80, 15 dims)")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(4, dynamic=dynamic))
    return lines


def generate_phase5_tasks(repo_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 5 detailed tasks (Verification & Delivery + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 5 Tasks: Verification & Delivery")
    lines.append("")
    lines.append("### Phase 5 Overview")
    lines.append("Phase 5 verifies the system against test results, ensuring all FRs meet acceptance criteria.")
    lines.append("Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). No harness run-gate — P5 was cleared by Gate 3 at P4 exit. However, advance-phase still enforces TDD-PRECHECK (pytest 100% + D4 spec-coverage ≥80%) before FSM transition.")
    lines.append("")
    lines.append(
        "> If code changes are needed for any FR (e.g., bug fixes found during verification), "
        "run full TDD: `run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. "
        "Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from "
        "GATE1-DELTA to full TDD when needed."
    )
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids if not dynamic else [], phase=5))
    lines.extend(_entry_gate_check(5))
    lines.extend(_preflight_steps(5))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(5))
        lines.extend(_dynamic_fr_template_block(5))
    elif manifest_fr_ids:
        lines.append("### FR Verification Tasks ({} total)".format(len(manifest_fr_ids)))
        lines.append("")
        for fr_id in manifest_fr_ids:
            lines.append(f"#### {fr_id}: Verification")
            lines.append(f"- [ ] Confirm all acceptance criteria from SRS.md are met for {fr_id}")
            lines.append(f"- [ ] Run integration tests for {fr_id}")
            lines.append("- [ ] Verify edge cases and error paths")
            lines.append("- [ ] Confirm ≥80% branch coverage")
            lines.append("")
            lines.extend(_fr_carryforward_steps(fr_id, phase=5))
    else:
        lines.append("### Verification Items")
        lines.append("(No FR list found — add per-FR verification steps based on SRS.md)")
        lines.append("")
    lines.extend([
        "### P5 System Verification",
        "",
        "- [ ] **[BASELINE]** Generate `05-verification/BASELINE.md` (system state snapshot):",
        "  - Document: current version, test results summary, coverage %, Gate 3 composite score",
        "  - Reference: `04-testing/TEST_RESULTS.md` and `03-development/src/` module list",
        "- [ ] **[VERIFY-REPORT]** Generate `05-verification/VERIFICATION_REPORT.md`:",
        "  - For each FR: verification status, acceptance criteria result (PASS/FAIL), evidence",
        "  - Include: test coverage %, mutation score, deferred issues from Gate 3",
        "  - Certify: all Gate 3 open issues addressed or deferred with justification",
        "- [ ] Re-run integration tests: `pytest tests/integration/ -q` (or equivalent per NFRs)",
        "- [ ] Confirm performance NFRs met: review benchmark entries in `04-testing/TEST_RESULTS.md`",
        "- [ ] Re-run security scan clean: `bandit -r 03-development/src/ -ll` + `gitleaks detect`",
        "",
    ])

    lines.extend([
        "### P5 Milestone Push (10-Push Strategy ⑦)",
        "",
        "- [ ] **PUSH ⑦ — P5-baseline** (after BASELINE.md is generated):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p5-baseline --project .",
        "  ```",
        "  > Writes HANDOVER.md + commits + pushes.",
        "",
    ])

    lines.append("### Phase 5 Deliverables")
    lines.append("- [ ] `05-verification/BASELINE.md` - System baseline")
    lines.append("- [ ] `05-verification/VERIFICATION_REPORT.md` - Verification report")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(5, dynamic=dynamic))
    return lines


def generate_phase6_tasks(repo_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 6 detailed tasks (Quality Assurance — Gate 4 full replacement)"""
    lines = []
    lines.append("## Phase 6 Tasks: Quality Assurance")
    lines.append("")
    lines.append("### Phase 6 Overview")
    lines.append("Phase 6 centres on Gate 4 — the full-project quality evaluation.")
    lines.append("No FR loop. Gate 4 = tool-scored automated evaluation (15 dims incl. traceability, CRG recon) PLUS")
    lines.append("Agent B peer review of the QA deliverables (HR-01) — both are required to exit.")
    lines.append("")

    # P6 has exactly one checkpoint: Gate 4
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint saved):")
    lines.append("> - CHECKPOINT-GATE-4: Gate 4 (Full Project — 15 dims) + Agent B peer review")
    lines.append("")

    lines.extend(_entry_gate_check(6))
    lines.extend(_preflight_steps(6))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(6, has_fr_template=False))

    lines.append("### P6 Phase End Audit (+ A/B Review)")
    lines.append("")
    lines.append("> A/B collaboration is active for Phase 6 deliverables (HR-01).")
    lines.append("> Agent A generates QUALITY_REPORT.md and RELEASE_NOTES.md.")
    lines.append("> Agent B (ARCHITECT) reviews the deliverables and verifies Gate 4 score.")
    lines.append("")

    # Only embed static quality metrics from QUALITY_REPORT.md in non-dynamic mode.
    # In dynamic mode the plan is generated at project start (before any Gate 4 data exists),
    # so parsing the current QUALITY_REPORT.md would embed stale project-specific values.
    if not dynamic:
        qr = parse_quality_report(repo_path)
        if qr.get('metrics'):
            lines.append("### Existing Quality Metrics (from QUALITY_REPORT.md)")
            lines.append("")
            for metric, value in qr['metrics']:
                lines.append(f"- **{metric}**: {value}")
            lines.append("")

    lines.append("### Pre-Gate Preparation")
    lines.append("- [ ] Confirm all FRs are merged to main branch")
    lines.append("- [ ] Confirm no open critical or high issues from Gate 3")
    lines.append("")

    lines.extend(_gate4_prerequisites_block())

    lines.extend(_gate_exit_checkpoint(gate_num=4, phase=6, checkpoint_n=1))

    lines.append("### Phase 6 Deliverables")
    lines.append("- [ ] Gate 4 PASS (composite ≥ 85, all 15 dims, CRG recon done)")
    lines.append("- [ ] `06-quality/QUALITY_REPORT.md` - Quality report (auto-generated by Gate 4)")
    lines.append("- [ ] `RELEASE_NOTES.md` - Release notes")
    lines.append("- [ ] `FINAL_SIGN_OFF.md` - Final sign-off")
    lines.append(_sessions_spawn_deliverable())
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(6, dynamic=dynamic))
    return lines


def generate_phase7_tasks(repo_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 7 detailed tasks (Risk Management + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 7 Tasks: Risk Management")
    lines.append("")
    lines.append("### Phase 7 Overview")
    lines.append("Phase 7 identifies, tracks, and mitigates all risks introduced during development.")
    lines.append("Each FR gets a Gate 1 risk-aware re-evaluation (CHECKPOINT). No harness run-gate — P7 cleared by Gate 4. However, advance-phase still enforces TDD-PRECHECK (pytest 100% + D4 spec-coverage ≥90%) before FSM transition.")
    lines.append("")
    lines.append(
        "> If risk mitigation requires code changes to any FR, run full TDD: "
        "`run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. "
        "Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from "
        "GATE1-DELTA to full TDD when needed."
    )
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids if not dynamic else [], phase=7))
    lines.extend(_entry_gate_check(7))
    lines.extend(_preflight_steps(7))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(7))
        lines.extend(_dynamic_fr_template_block(7))
        lines.extend([
            "### P7 Risk Register Generation",
            "",
            "> Generate risk deliverables ONCE before per-FR evaluation (orchestrator runs directly).",
            "",
            "- [ ] **[RISK-REGISTER]** Generate `07-risk/RISK_REGISTER.md`:",
            "  - Review open issues from Gate 3/4, `deferred_fixes.md`, and `.sessi-work/issue_registry.json`",
            "  - For each risk: ID, name, likelihood (1–5), impact (1–5), category, mitigation approach",
            "- [ ] **[RISK-MITIGATION]** Generate `07-risk/RISK_MITIGATION_PLANS.md`:",
            "  - For HIGH risks (likelihood × impact ≥ 9): write formal mitigation plan with owner + deadline",
            "- [ ] **[RISK-STATUS]** Generate `07-risk/RISK_STATUS_REPORT.md`:",
            "  - Summary of all risks, current status, mitigation owner, target date",
            "",
        ])
    else:
        risks = parse_risk_register(repo_path)
        if risks:
            lines.append("### Risk Register ({} total)".format(len(risks)))
            lines.append("")
            for risk in risks:
                lines.append(f"- **{risk['name']}**: Define likelihood/impact scores and mitigation approach → document in RISK_REGISTER.md")
            lines.append("")
        else:
            lines.append("### Risk Categories")
            lines.append("- Technical risks")
            lines.append("- Schedule risks")
            lines.append("- Resource risks")
            lines.append("- External risks")
            lines.append("")

        if manifest_fr_ids:
            lines.append("### FR Risk Evaluation ({} total)".format(len(manifest_fr_ids)))
            lines.append("")
            for fr_id in manifest_fr_ids:
                lines.append(f"#### {fr_id}: Risk Assessment")
                lines.append(f"- [ ] Review open issues from previous gates for {fr_id}")
                lines.append(f"- [ ] Check `deferred_fixes.md` for {fr_id} entries")
                lines.append("- [ ] Confirm no new defects introduced")
                lines.append("")
                lines.extend(_fr_carryforward_steps(fr_id, phase=7))
        else:
            lines.append("(No FR list found in manifest — run Gate 1 per FR manually)")
            lines.append("")

    lines.extend([
        "### P7 Milestone Push (10-Push Strategy ⑨)",
        "",
        "- [ ] **PUSH ⑨ — P7 exit** (after risk register is complete):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p7 --project .",
        "  ```",
        "  > Writes HANDOVER.md + commits + pushes.",
        "",
    ])

    lines.append("### Phase 7 Deliverables")
    lines.append("- [ ] `07-risk/RISK_REGISTER.md` - Risk register")
    lines.append("- [ ] `07-risk/RISK_MITIGATION_PLANS.md` - Mitigation plans")
    lines.append("- [ ] `07-risk/RISK_STATUS_REPORT.md` - Risk status report")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(7, dynamic=dynamic))
    return lines


def generate_phase8_tasks(repo_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 8 detailed tasks (Configuration Management + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 8 Tasks: Configuration Management")
    lines.append("")
    lines.append("### Phase 8 Overview")
    lines.append("Phase 8 establishes a complete configuration management system ensuring traceability.")
    lines.append("Each FR gets a Gate 1 config-aware re-evaluation (CHECKPOINT). No harness run-gate — P8 cleared by Gate 4. However, advance-phase still enforces TDD-PRECHECK (pytest 100% + D4 spec-coverage ≥90%) before FSM transition.")
    lines.append("")
    lines.append(
        "> If configuration changes require code modifications to any FR, run full TDD: "
        "`run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. "
        "Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from "
        "GATE1-DELTA to full TDD when needed."
    )
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids if not dynamic else [], phase=8))
    lines.extend(_entry_gate_check(8))
    lines.extend(_preflight_steps(8))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(8))
        lines.extend(_dynamic_fr_template_block(8))
        lines.extend([
            "### P8 Configuration Records Generation",
            "",
            "> Generate config deliverables ONCE before push-milestone (orchestrator runs directly).",
            "",
            "- [ ] **[CONFIG-RECORDS]** Generate `CONFIG_RECORDS.md` in `08-config/` directory:",
            "  - Review all env vars, secrets, feature flags, and deployment settings",
            "  - For each config item: name, value/source, access method, owner, environment (dev/staging/prod)",
            "  - Reference: `03-development/src/` module configs + any `.env.example` or `settings.py`",
            "- [ ] **[RELEASE-CHECKLIST]** Generate `RELEASE_CHECKLIST.md` in `08-config/` directory:",
            "  - Pre-release: all Gate 4 dims PASS, no open critical issues, security scan clean",
            "  - Deployment: env vars set, secrets rotated, DB migrations run, smoke tests pass",
            "  - Post-release: monitoring alerts configured, rollback plan documented",
            "",
        ])
    else:
        configs = parse_config_records(repo_path)
        if configs:
            lines.append("### Configuration Items ({} total)".format(len(configs)))
            lines.append("")
            for config in configs:
                lines.append(f"- **{config['name']}**: Document value/source/access method → update CONFIG_RECORDS.md")
            lines.append("")
        else:
            lines.append("### Configuration Categories")
            lines.append("- Environment configuration")
            lines.append("- Deployment configuration")
            lines.append("- Security configuration")
            lines.append("- Monitoring configuration")
            lines.append("")

        if manifest_fr_ids:
            lines.append("### FR Configuration Evaluation ({} total)".format(len(manifest_fr_ids)))
            lines.append("")
            for fr_id in manifest_fr_ids:
                lines.append(f"#### {fr_id}: Configuration Record")
                lines.append(f"- [ ] Confirm {fr_id} configuration items are documented in CONFIG_RECORDS.md")
                lines.append("- [ ] Confirm environment variables / secrets are managed (not hardcoded)")
                lines.append(f"- [ ] Confirm deployment checklist entries for {fr_id}")
                lines.append("")
                lines.extend(_fr_carryforward_steps(fr_id, phase=8))
        else:
            lines.append("(No FR list found in manifest — run Gate 1 per FR manually)")
            lines.append("")

    lines.extend([
        "### P8 Archive — REQUIRED before push-milestone (CI p8-archive-check)",
        "",
        "- [ ] **[P8-ARCHIVE]** Create `.methodology-archive/` directory (required for CI `p8-archive-check`):",
        "  ```bash",
        "  mkdir -p .methodology-archive",
        "  cp -r .sessi-work/ .methodology-archive/",
        "  ```",
        "  > Must run BEFORE `push-milestone --type p8`; `_validate_p8_completion()` in push-milestone auto-verifies.",
        "  > CI job `p8-archive-check` also validates this directory on push to main.",
        "",
        "- [ ] **[P8-HANDOVER-CHECK]** Verify `HANDOVER.md` has no Phase 9 references (validated by CI `p8-archive-check`):",
        "  ```bash",
        '  grep -qi "phase 9\\|phase9\\|phase9_plan" HANDOVER.md \\',
        '    && echo "ERROR: Phase 9 refs found — remove them" \\',
        '    || echo "OK: no Phase 9 refs"',
        "  ```",
        "  Phase 8 is the final phase. Any Phase 9 references must be removed.",
        "",
    ])

    lines.extend([
        "### P8 Milestone Push (10-Push Strategy ⑩)",
        "",
        "- [ ] **PUSH ⑩ — P8 exit** (after config records are complete):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p8 --project .",
        "  ```",
        "  > Writes HANDOVER.md + commits + pushes. Pipeline complete.",
        "",
    ])

    lines.append("### Phase 8 Deliverables")
    lines.append("- [ ] `CONFIG_RECORDS.md` - Configuration records")
    lines.append("- [ ] `RELEASE_CHECKLIST.md` - Release checklist")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(8, dynamic=dynamic))
    return lines


# ============================================================================
# Main Generator
# ============================================================================

def generate_full_plan(phase: int, repo_path: Path, output_path: Optional[Path] = None,
                       dynamic: bool = False, force: bool = False) -> Optional[str]:
    """Generate full plan with phase-specific detailed tasks.

    Idempotency guard: if *output_path* already exists and contains completed
    checklist items (`- [x]`), the plan is NOT overwritten unless *force* is True.
    This prevents `plan-all`/`plan-phase` re-runs from wiping in-progress marks on
    a phase that is already underway. Returns the existing content unchanged in
    that case.
    """
    if output_path and output_path.exists() and not force:
        try:
            _existing = output_path.read_text(encoding="utf-8")
        except OSError:
            _existing = ""
        if "- [x]" in _existing:
            print(
                f"[SKIP] Phase {phase}: {output_path.name} has progress marks — "
                "preserved (use --force to regenerate)."
            )
            return _existing

    srs_paths = [
        repo_path / "01-requirements" / "SRS.md",
    ]
    srs_path = next((p for p in srs_paths if p.exists()), None)

    # Phase 2-4 need existing SRS; dynamic mode skips this requirement
    if srs_path is None and phase in (2, 3, 4) and not dynamic:
        print(f"[ERROR] SRS.md not found for phase {phase}")
        return None
    _srs = cast(Path, srs_path)  # safe: phases 1 and 5-8 don't use srs_path; dynamic skips it

    generators = {
        1: lambda: generate_phase1_tasks(repo_path, _srs, dynamic=dynamic),
        2: lambda: generate_phase2_tasks(repo_path, _srs, dynamic=dynamic),
        3: lambda: generate_phase3_tasks(repo_path, _srs, dynamic=dynamic),
        4: lambda: generate_phase4_tasks(repo_path, _srs, dynamic=dynamic),
        5: lambda: generate_phase5_tasks(repo_path, dynamic=dynamic),
        6: lambda: generate_phase6_tasks(repo_path, dynamic=dynamic),
        7: lambda: generate_phase7_tasks(repo_path, dynamic=dynamic),
        8: lambda: generate_phase8_tasks(repo_path, dynamic=dynamic),
    }

    generator = generators.get(phase)
    if not generator:
        print(f"Unknown phase: {phase}")
        return None

    print(f"Generating Phase {phase} tasks...")

    task_lines = generator()

    phase_names = {
        1: "Requirements Specification",
        2: "Architecture Design",
        3: "Implementation",
        4: "Testing",
        5: "Verification & Delivery",
        6: "Quality Assurance",
        7: "Risk Management",
        8: "Configuration Management",
    }

    mode_line = ["> **Mode**: Dynamic (load-context at execution time)", ""] if dynamic else []
    plan_lines = [
        f"# Phase {phase} Full Execution Plan -- {repo_path.name}",
        "",
        f"> **Version**: v{_HARNESS_VERSION} (project plan)",
        f"> **Project**: {repo_path.name}",
        f"> **Date**: {datetime.now().strftime('%Y-%m-%d')}",
        f"> **Framework**: harness-methodology v{_HARNESS_VERSION}",
        f"> **Phase**: {phase} - {phase_names.get(phase, 'Unknown')}",
        f"> **Status**: Full version (including Phase {phase} detailed tasks)",
        *mode_line,
        "",
        "---",
        "",
    ]

    plan_lines.extend(task_lines)

    plan_text = '\n'.join(plan_lines)

    if output_path:
        output_path.write_text(plan_text, encoding='utf-8')
        print(f"Full plan saved to: {output_path}")

    return plan_text


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Generate full plan with phase-specific detailed tasks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 scripts/generate_full_plan.py --phase 3 --repo /path/to/project
    python3 scripts/generate_full_plan.py --phase 5 --repo /path/to/project --output phase5_FULL.md
        """
    )
    parser.add_argument('--phase', type=int, required=True, help='Phase number (1-8)')
    parser.add_argument('--repo', type=str, required=True, help='Repository path')
    parser.add_argument('--output', type=str, help='Output file path')
    parser.add_argument('--no-output', action='store_true', help='Print to stdout instead of saving to file')

    args = parser.parse_args()

    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"Repository not found: {repo_path}")
        return 1

    output_path = Path(args.output) if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    plan = generate_full_plan(args.phase, repo_path, output_path)

    if plan:
        if args.no_output:
            print(plan)
        else:
            print(f"\nFull plan generated ({len(plan)} chars)")
            print(plan[:1500])
        return 0
    else:
        return 1


if __name__ == '__main__':
    sys.exit(main())

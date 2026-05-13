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

        config_pattern = re.compile(r'\|\s*([^\|]+)\s*\|.*?\|.*?\|', re.MULTILINE)
        configs = []
        for m in config_pattern.finditer(content):
            config_name = m.group(1).strip()
            if config_name and len(config_name) > 3:
                configs.append({'name': config_name})

        if configs:
            return configs[:20]

    return []


def parse_srs_nfr_sections(srs_path: Optional[Path]) -> List[Dict]:
    """Parse SRS.md to extract NFR sections"""
    if srs_path is None:
        return []
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

    return nfrs


# ============================================================================
# Gate Step Helpers (two-phase evaluation: run-gate → evaluate → finalize-gate)
# ============================================================================

# Phase → gate applicability
_PHASE_GATE1_PHASES: frozenset = frozenset({3, 4, 5, 7, 8})   # Gate 1 per-FR
_PHASE_EXIT_GATES: dict = {3: 2, 4: 3, 6: 4}                  # phase → exit gate num

# Gate metadata: (score_gate, dim_count, notes)
_GATE_META: dict = {
    1: (None, 3,  "linting(90) · type_safety(85) · test_coverage(80)"),
    2: (75,   7,  "linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70)"),
    3: (80,   12, "linting(90) · type_safety(85) · test_coverage(80) · security(80) · secrets_scanning(100) · license_compliance(100) · mutation_testing(70) · architecture(80) · readability(80) · error_handling(80) · documentation(75) · performance(75)  [CRG recon inside run-gate]"),
    4: (85,   12, "same 12 dims as Gate 3, score_gate ≥ 85  [CRG recon inside run-gate · Hermes APPROVE required]"),
}

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
    1: ["Project description / stakeholder brief", "draft docs/SRS.md (full content)"],
    2: ["docs/SRS.md (full)", "docs/CONSTRAINTS.md (full)", "draft docs/SAD.md (full)", "draft docs/ADR.md (full)"],
    3: ["docs/SRS.md §FR-XX section", "docs/SAD.md module spec for FR-XX", "src/…/fr_xx.py (implemented code + tests)"],
    4: ["docs/SRS.md §FR-XX section", "docs/SAD.md module spec", "src/…/fr_xx.py", "tests/…/test_fr_xx.py", "TEST_PLAN.md entry for FR-XX"],
    5: ["docs/SRS.md acceptance criteria for FR-XX", "src/…/fr_xx.py", "tests/…/test_fr_xx.py", "TEST_RESULTS.md entry"],
    6: ["docs/SRS.md", "docs/SAD.md", "QUALITY_REPORT.md (draft)", "relevant src/ sections"],
    7: ["docs/SRS.md", "RISK_REGISTER.md (draft)"],
    8: ["docs/SRS.md", "CONFIG_RECORDS.md (draft)", "ops/ relevant configs"],
}

# Agent B review checklist per phase
_AGENT_B_CHECKS: Dict[int, List[str]] = {
    1: ["All FRs testable? (no vague criteria)", "NFRs measurable?", "No contradictions between FRs?", "Every stakeholder need covered?"],
    2: ["Every FR maps to ≥1 module?", "NFRs addressed (latency/security/cost)?", "No circular dependencies?", "ADR covers all major decisions?"],
    3: ["Code matches SRS acceptance criteria?", "Tests actually test the spec (not the impl)?", "No forbidden patterns (app/infrastructure/, @covers: L1 Error)?", "Docstrings have [FR-XX] tag + Citations?"],
    4: ["Test coverage ≥80% for this FR?", "Edge cases covered?", "Results match TEST_PLAN.md expected outcomes?"],
    5: ["Acceptance criteria fully met?", "No regressions in related FRs?"],
    6: ["All 12 Gate 4 dimensions addressed?", "Critical issues count = 0?", "Score ≥85 achievable?"],
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
            "embed_docs": ["Project description / stakeholder brief", "draft docs/SRS.md (full content)"],
        },
        {
            "label": "CONSTRAINTS.md",
            "desc": "Technical Constraints — technology stack, SLA targets, cost model, regulatory requirements",
            "depends_on": ["SRS.md"],
            "task_hint": "Analyze constraints from SRS → document tech stack, SLA, cost model, compliance → validate completeness",
            "checks": ["All technical constraints documented?", "SLA targets defined and measurable?",
                       "Cost model complete?", "Constraints consistent with SRS requirements?"],
            "embed_docs": ["docs/SRS.md (APPROVED — full content)",
                           "draft docs/CONSTRAINTS.md (full content)"],
        },
        {
            "label": "SPEC_TRACKING.md",
            "desc": "Spec Tracking Matrix — maps every FR to its current status, owner, and acceptance state",
            "depends_on": ["SRS.md"],
            "task_hint": "Build spec tracking matrix from SRS.md FRs → assign status/owner per FR → validate completeness",
            "checks": ["Every FR from SRS.md listed?", "Status field populated per FR?",
                       "Owner assigned per FR?", "No orphan FRs (in SRS but not tracked)?"],
            "embed_docs": ["docs/SRS.md (APPROVED — full content)",
                           "draft docs/SPEC_TRACKING.md (full content)"],
        },
        {
            "label": "TRACEABILITY_MATRIX.md",
            "desc": "Requirements Traceability Matrix — bidirectional traceability from FRs through design to tests",
            "depends_on": ["SRS.md", "SPEC_TRACKING.md"],
            "task_hint": "Build bidirectional traceability matrix → link FRs → design elements → test cases → validate coverage",
            "checks": ["Bidirectional traceability established? (FR→design→test and back)",
                       "Every FR has ≥1 downstream link?", "No orphan requirements?",
                       "Coverage complete (all FRs traceable)?"],
            "embed_docs": ["docs/SRS.md (APPROVED — full content)",
                           "docs/SPEC_TRACKING.md (APPROVED — full content)",
                           "draft docs/TRACEABILITY_MATRIX.md (full content)"],
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
            "embed_docs": ["docs/SRS.md (full)", "docs/CONSTRAINTS.md (full)",
                           "draft docs/SAD.md (full)"],
        },
        {
            "label": "ADR.md",
            "desc": "Architecture Decision Records — key decisions with context, options, rationale",
            "depends_on": ["SAD.md"],
            "task_hint": "Document key architecture decisions → write ADR.md → validate each decision references SAD context",
            "checks": ["ADR covers all major decisions from SAD.md?",
                       "Each ADR has context + options + rationale?",
                       "No decisions contradict SAD.md module mapping?"],
            "embed_docs": ["docs/SRS.md (full)", "docs/SAD.md (APPROVED — full content)",
                           "draft docs/ADR.md (full)"],
        },
        {
            "label": "ARCHITECTURE_DIAGRAM.md",
            "desc": "Architecture diagram — system topology, deployment view, data flow visualization",
            "depends_on": ["SAD.md", "ADR.md"],
            "task_hint": "Create architecture diagrams → system topology + deployment + data flow → validate against SAD/ADR",
            "checks": ["Topology matches SAD.md component layout?",
                       "Deployment view consistent with ADR infrastructure decisions?",
                       "Data flows match SAD.md interface definitions?"],
            "embed_docs": ["docs/SAD.md (APPROVED — full content)",
                           "docs/ADR.md (APPROVED — full content)",
                           "draft docs/ARCHITECTURE_DIAGRAM.md (full content)"],
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

    lines: List[str] = [
        f"- [ ] **[B-1]** Agent B ({role_b}){fr_suffix} — dispatch as **STATELESS** subagent:",
        "  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.",
        "  > NEVER write 'read docs/SRS.md' in the prompt — it will fail silently.",
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
        f"  You are {role_b}. Your task: review the following deliverable{fr_suffix}.",
        "  You have NO access to any files — all context is provided below.",
        "",
    ]
    # Auto-enumerate actual doc titles so the agent doesn't have to map them manually
    for i, doc in enumerate(embed_docs, 1):
        lines += [
            f"  === [DOC {i}: {doc}] ===",
            "  {paste full content here}",
            "",
        ]
    lines += [
        "  Review checklist:",
    ]
    for check in checks:
        lines.append(f"  - {check}")
    lines += [
        "",
        "  Return JSON only:",
        '  {"status":"STAGE_PASS"|"REJECT","review_status":"APPROVE"|"REJECT",',
        '   "reason":"...","confidence":1-10,"citations":["file:line"],"gaps":[...]}',
        "  ```",
        "",
        "- [ ] **[B-2]** Agent B returns JSON — parse `review_status`:",
        "  - `APPROVE` → continue to next step",
        "  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).",
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
        "**A/B Work** (HR-01: A≠B · HR-04: HybridWorkflow ON · HR-10: log required):",
        f"- [ ] **[A-1]** Agent A ({role_a}): {task_hint}",
        "  - FORBIDDEN: vague/non-testable acceptance criteria",
        "- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`",
    ]

    # Agent B stateless dispatch block (customized per deliverable)
    lines += [
        f"- [ ] **[B-1]** Agent B ({role_b}) — dispatch as **STATELESS** subagent:",
        "  > ⚠️  **STATELESS SANDBOX**: Agent B has ZERO access to local files or /tmp.",
        "  > NEVER write 'read docs/SRS.md' in the prompt — it will fail silently.",
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
            "  {paste full content here}",
            "",
        ]
    lines += [
        "  Review checklist:",
    ]
    for check in checks:
        lines.append(f"  - {check}")
    lines += [
        "",
        "  Return JSON only:",
        '  {"status":"STAGE_PASS"|"REJECT","review_status":"APPROVE"|"REJECT",',
        '   "reason":"...","confidence":1-10,"citations":["file:line"],"gaps":[...]}',
        "  ```",
        "",
        "- [ ] **[B-2]** Agent B returns JSON — parse `review_status` **AND** `gaps` severity:",
    ]
    if sub_n < total:
        next_action = f"continue to Sub-Task {sub_n + 1}/{total}"
    else:
        next_action = "all deliverables complete; proceed to Human Peer Review"
    lines += [
        f"  - `APPROVE` + all gaps are `low` → {next_action}",
        "  - `APPROVE` + any gap is `medium` or `high` → fix gaps → **re-dispatch B as round 2**",
        f"    (embed same docs as B-1 above, replacing `{label}` with its updated content)",
        f"    → {next_action} only after round-2 APPROVE",
        "  - `REJECT` → Agent A fixes gaps → re-dispatch B. Max 5 rounds (HR-12).",
        "",
        "  > ⚠️ **BLOCKING**: Do NOT start the next Sub-Task until this sub-task's current",
        "  > round is fully APPROVED (including any required round 2).",
        "  > AgentSpawner auto-logs round-2 re-dispatch to `sessions_spawn.log` (HR-10).",
        "",
        f"  > fr_id uses P{phase} as phase-level placeholder; replace with FR-XX for FR-specific plans.",
        "",
    ]
    return lines


def _preflight_steps(phase: int) -> List[str]:
    """Preflight hook step — run before the FR development loop (FSM + Constitution check + CI readiness)."""
    if phase == 1:
        ci_check = [
            "- [ ] **[PREFLIGHT-CI]** ⛔ HARD STOP if any item below is missing — complete SKILL.md §0.1 Step 0 first:",
            "  1. `git config quality.phase` returns `1`  ← set by `init-project`",
            "  2. `.github/workflows/harness_quality_gate.yml` exists in project root  ← set by `init-project`",
            "  3. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)  ← set by `init-project`",
            "  4. GitHub repo variable `CURRENT_PHASE = 1` (Settings → Variables)  ← optional (fallback '1')",
            "  5. `HERMES_REVIEWER_TARGET` exported in shell  ← required",
            "  If any required item (1-3, 5) is missing: stop, run `python3 harness_cli.py init-project --phase 1 --project $REPO`, then set manual items.",
        ]
    else:
        ci_check = [
            "- [ ] **[PREFLIGHT-CI]** Confirm CI wiring unchanged (should be set since P1):",
            "  1. `.github/workflows/harness_quality_gate.yml` exists",
            "  2. Git hooks installed (`ls .git/hooks/prepare-commit-msg`)",
            "  3. harness importable (submodule, PYTHONPATH, or vendored `quality_gate/`)",
            f"  4. GitHub repo variable `CURRENT_PHASE` = {phase} (updated by `advance-phase`)",
            f"  > If stale: run `python3 harness_cli.py init-project --phase {phase} --project $REPO --force`",
        ]
    return [
        "### Pre-Phase Preflight",
        "",
        "- [ ] **[PREFLIGHT]** Run phase hooks (FSM, Constitution, Kill-Switch, Drift, CI Readiness):",
        "  ```bash",
        f"  python3 harness_cli.py run-phase --phase {phase} --project $REPO",
        "  ```",
        "  If FAILED non-critically: use `--force`. If BLOCKED: fix FSM/Constitution first.",
        "",
        *ci_check,
        "",
    ]


def _entry_gate_check(phase: int) -> List[str]:
    """Entry condition check — confirm previous phase exit gate before starting any work."""
    _ENTRY_MAP: dict = {
        2: ("P1 human APPROVE",
            "git log contains commit 'phase1(human-review): Phase 1 deliverables APPROVED'"),
        3: ("P2 human APPROVE",
            "git log contains commit 'phase2(human-review): Phase 2 deliverables APPROVED'"),
        4: ("Gate 2 PASS",
            ".methodology/quality_manifest.json records Gate 2 PASS from P3"),
        5: ("Gate 3 PASS",
            ".methodology/quality_manifest.json records Gate 3 PASS from P4"),
        6: ("Gate 3 PASS",
            ".methodology/quality_manifest.json records Gate 3 PASS from P4"),
        7: ("Gate 4 PASS",
            ".methodology/quality_manifest.json records Gate 4 PASS from P6"),
        8: ("Gate 4 PASS",
            ".methodology/quality_manifest.json records Gate 4 PASS from P6"),
    }
    if phase not in _ENTRY_MAP:
        return []
    gate_label, proof = _ENTRY_MAP[phase]
    prev_phase = phase - 1
    return [
        "### Entry Gate Verification",
        "",
        f"- [ ] **[ENTRY-CHECK]** Confirm Phase {prev_phase} exit ({gate_label}) before proceeding (HR-03 — no phase skips):",
        f"  Proof: {proof}.",
        f"  If NOT confirmed: return to Phase {prev_phase} and complete exit gate first.",
        "",
    ]


def _human_checkpoint(phase: int, checkpoint_n: int) -> List[str]:
    """Human peer-review checkpoint for P1/P2 (deliverable review — NOT harness run-gate)."""
    _DELIVERABLES: dict = {
        1: ["01-requirements/SRS.md", "01-requirements/CONSTRAINTS.md",
            "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
        2: ["02-architecture/SAD.md", "02-architecture/adr/ADR.md",
            "02-architecture/ARCHITECTURE_DIAGRAM.md"],
    }
    artifacts = _DELIVERABLES.get(phase, [])
    return [
        "",
        f"### 🔒 CHECKPOINT-{checkpoint_n}: Human Peer Review — Phase {phase} Exit",
        "> Phase 1/2 exit gate = human document review (NOT `harness run-gate --gate 1`).",
        "> APPROVE criteria: all FRs addressed, no critical gaps, terminology consistent.",
        "",
        "- [ ] **[HR-READ]** Reviewer reads all deliverables:",
        *([f"  - `{a}`" for a in artifacts]),
        "  - Checklist: All FRs covered? No contradictions? Each item testable/traceable?",
        "- [ ] **[HR-DECIDE]** Reviewer records decision:",
        "  ```json",
        f'  {{"phase": {phase}, "reviewer": "XXXX", "status": "APPROVE", "reason": "..."}}',
        "  ```",
        "  - If REJECT → author fixes → re-review. Max 5 rounds (HR-12).",
        f"- [ ] **[HR-PUSH]** ✅ Push to GitHub + HANDOVER.md (CHECKPOINT-{checkpoint_n} saved):",
        "  ```bash",
        f"  python3 harness_cli.py push-checkpoint --phase {phase} --project . \\",
        "    --fr-ids FR-01,FR-02,FR-03",
        "  ```",
        "  > This writes `HANDOVER.md` (crash-recovery checkpoint) to project root,",
        "  > then commits + pushes all changes to origin.",
        "  > After a crash, read HANDOVER.md first — it tells you where you were.",
        "",
    ]


def _fr_dev_steps(fr_id: str, phase: int) -> List[str]:
    """A/B development steps for one FR — must appear BEFORE Gate 1 checkpoint."""
    role_a, role_b, task_hint = _PHASE_ROLES.get(
        phase, ("DEVELOPER", "REVIEWER", "Implement per SRS + SAD")
    )
    lines = [
        f"**A/B Work — {fr_id}** (HR-01: A≠B · HR-04: HybridWorkflow ON · HR-10: log required):",
        f"- [ ] **[A-1]** Agent A ({role_a}): {task_hint}",
        f"  - Docstrings: `[{fr_id}]` tag + `Citations:` with line numbers (HR-15)",
        "  - FORBIDDEN: `app/infrastructure/` · `@covers: L1 Error` · `@type: edge`",
        "- [ ] **[A-2]** Agent A returns `{status, files, confidence, citations, summary}`",
    ]
    lines.extend(_agent_b_dispatch_block(phase, role_b, fr_id=fr_id))
    lines.extend([
        "  > ℹ️ `sessions_spawn.log` auto-populated by AgentSpawner on dispatch (HR-10).",
        "",
    ])
    return lines


def _phase_advance_step(phase: int) -> List[str]:
    """Instruction to advance to the next phase after all checkpoints PASS."""
    if phase >= 8:
        return [
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
    return [
        f"### Phase {phase} → Phase {next_phase}: {next_name}",
        "",
        "- [ ] Confirm ALL checkpoints in this plan are ✓  (no skips — HR-03)",
        "- [ ] Verify `HANDOVER.md` exists at project root (written by `push-checkpoint`)",
        f"- [ ] Generate Phase {next_phase} plan:",
        "  ```bash",
        f"  python3 harness_cli.py plan-phase --phase {next_phase} --project $REPO \\",
        f"    --output $REPO/.methodology/phase{next_phase}_plan.md",
        "  ```",
        f"- [ ] Open `phase{next_phase}_plan.md` and follow from the top.",
        f"- [ ] If session crashes during Phase {next_phase}: read `HANDOVER.md` or run `generate-next-plan`",
        "",
    ]


def _p3_milestone_push_steps(fr_ids: List[str]) -> List[str]:
    """P3 milestone push instructions (PUSH ③ at ≥50% FRs, PUSH ④ pre-SSI)."""
    if not fr_ids:
        return []
    total = len(fr_ids)
    mid = max(1, total // 2)
    mid_ids = ",".join(fr_ids[:mid])
    full_ids = ",".join(fr_ids)
    # Visual truncated representation for the plan text (not used in bash commands)
    if len(fr_ids) > 5:
        _visual = ",".join(fr_ids[:5]) + f",…+{len(fr_ids) - 5}"
    else:
        _visual = full_ids
    return [
        "### P3 Milestone Pushes (10-Push Strategy ③④)",
        "",
        "> Per-FR Gate 1 only commits locally. The two **milestone pushes** below",
        "> write `HANDOVER.md` and push to origin — these are the crash-recovery checkpoints.",
        f"> All FR IDs in this project: {_visual}",
        "",
        f"- [ ] **PUSH ③ — P3-mid** (trigger when ≥{mid}/{total} FRs have Gate 1 PASS):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p3-mid --project . \\",
        f"    --fr-done {mid} --fr-total {total} --fr-ids {mid_ids}",
        "  ```",
        f"  > `--fr-ids` lists the FRs with Gate 1 PASS so far. Replace `{mid_ids}` with actual.",
        "  > Writes HANDOVER.md + commits + pushes. Next session reads HANDOVER.md to resume.",
        "",
        f"- [ ] **PUSH ④ — P3-pre-SSI** (trigger when all {total} FRs Gate 1 PASS, before SSI):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p3-pre-ssi --project . \\",
        f"    --fr-ids {full_ids}",
        "  ```",
        "  > Last stable snapshot before SSI modifies files. HANDOVER.md + push.",
        "",
    ]


def _gate1_checkpoint(fr_id: str, phase: int, checkpoint_n: int) -> List[str]:
    """Gate 1 evaluation steps for a single FR (local commit, no push)."""
    meta = _GATE_META[1]
    return [
        "",
        f"### 🔒 CHECKPOINT-{checkpoint_n}: Gate 1 — {fr_id}",
        f"> Dimensions: {meta[2]}",
        "> `gate1_result.json` is overwritten each FR — `finalize-gate` reads it immediately.",
        "",
        f"- [ ] **G1a** Prepare Gate 1 for {fr_id}:",
        "  ```bash",
        f"  python3 harness_cli.py run-gate --gate 1 --phase {phase} --fr-id {fr_id}",
        "  ```",
        "  Read the evaluation prompt printed above.",
        "",
        f"- [ ] **G1b** Evaluate all Gate 1 dimensions for {fr_id} inline:",
        "  - Follow `harness/ssi/prompts/evaluate_dimension.md`",
        "  - Write result to `.sessi-work/gate1_result.json`",
        "  - Schema: `harness/ssi/schemas/harness_gate_result.schema.json`",
        "",
        f"- [ ] **G1c** Finalize Gate 1 for {fr_id}:",
        "  ```bash",
        f"  python3 harness_cli.py finalize-gate --gate 1 --phase {phase} --fr-id {fr_id}",
        "  ```",
        "  **If FAIL** (any dim below threshold): fix code → repeat G1a→G1b→G1c until PASS.",
        "  **Do NOT proceed to G1d until all dims PASS.**",
        "",
        "- [ ] **G1d** ✅ Verify local commit saved (finalize-gate above already committed):",
        "  ```bash",
        "  git log --oneline -1",
        "  ```",
        "  > `finalize-gate --gate 1` calls `commit_fr_gate1()` — **local commit only, no push**.",
        "  > Push + HANDOVER.md happens at milestone: `push-milestone --type p3-mid` / `p3-pre-ssi` / Gate exit.",
        "",
    ]


def _gate_exit_checkpoint(gate_num: int, phase: int, checkpoint_n: int) -> List[str]:
    """Phase-exit gate evaluation steps (two-phase + push checkpoint)."""
    meta = _GATE_META[gate_num]
    crg_note = (
        "  (CRG recon triggered inside run-gate automatically — no separate action needed)"
        if gate_num in (3, 4) else ""
    )
    hermes_note = (
        f"  - **Hermes APPROVE required**: wait for reviewer APPROVE on Telegram before G{gate_num}d."
        if gate_num == 4 else ""
    )
    early_stop = [
        f"  **Early-stop cases after G{gate_num}c:**",
        f"  - CASE 1 PASS:     score ≥ score_gate AND critical==0 → `quality_complete=True` → G{gate_num}d",
        f"  - CASE 2 CONTINUE: score ≥ score_gate BUT issues remain → fix → repeat G{gate_num}a",
        "  - CASE 3 PLATEAU:  3 consecutive rounds, no new issues → `deferred_fixes.md` → proceed to push",
        "  - CASE 4 BLOCKED:  max_rounds exhausted, not PASS → `GateBlockedError` → escalate to human",
    ]
    return [
        "",
        f"### 🔒 CHECKPOINT-{checkpoint_n}: Gate {gate_num} — Phase {phase} Exit",
        f"> {meta[2]}",
        "",
        f"- [ ] **G{gate_num}a** Prepare Gate {gate_num}:",
        "  ```bash",
        f"  python3 harness_cli.py run-gate --gate {gate_num} --phase {phase}",
        "  ```",
        "  Read the evaluation prompt printed above.",
        *([crg_note] if crg_note else []),
        "",
        f"- [ ] **G{gate_num}b** Evaluate all Gate {gate_num} dimensions inline:",
        "  - Follow `harness/ssi/prompts/evaluate_dimension.md`",
        f"  - Write result to `.sessi-work/gate{gate_num}_result.json`",
        *(["  - Failing dim: fix code → re-evaluate → re-score"] if gate_num > 1 else []),
        "",
        f"- [ ] **G{gate_num}c** Finalize Gate {gate_num}:",
        "  ```bash",
        f"  python3 harness_cli.py finalize-gate --gate {gate_num} --phase {phase}",
        "  ```",
        *([hermes_note] if hermes_note else []),
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
    ]


def _checkpoint_index(fr_ids: List[str], phase: int) -> List[str]:
    """Generate a checkpoint index header for the plan (P3-P8)."""
    lines = [
        "> **Crash Recovery**: at each **milestone push**, `HANDOVER.md` is written to project root.",
        "> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.",
        "> Per-FR Gate 1 = **local commit only** (no push, no HANDOVER). Push happens at milestones.",
        "",
        "> **Checkpoint Index**:",
    ]
    cp = 1
    if phase in _PHASE_GATE1_PHASES:
        for fr_id in fr_ids:
            lines.append(f"> - CHECKPOINT-{cp}: Gate 1 / {fr_id} *(local commit)*")
            cp += 1
    if phase == 3:
        lines.append("> - MILESTONE: P3-mid push (≥50% FRs Gate 1 PASS) → **HANDOVER.md**")
        lines.append("> - MILESTONE: P3-pre-SSI push (all FRs done) → **HANDOVER.md**")
    if phase == 5:
        lines.append("> - MILESTONE: P5-baseline push (BASELINE.md generated) → **HANDOVER.md**")
    if phase == 7:
        lines.append("> - MILESTONE: P7 exit push (risk register complete) → **HANDOVER.md**")
    if phase == 8:
        lines.append("> - MILESTONE: P8 exit push (config records complete) → **HANDOVER.md**")
    if phase in _PHASE_EXIT_GATES:
        gate_num = _PHASE_EXIT_GATES[phase]
        lines.append(f"> - CHECKPOINT-{cp}: Gate {gate_num} (Phase {phase} Exit) → **push + HANDOVER.md**")
    elif phase == 6:
        lines.append(f"> - CHECKPOINT-{cp}: Gate 4 (Full Project, Hermes APPROVE) → **push + HANDOVER.md**")
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

def generate_phase1_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 1 detailed tasks (Requirements Specification).

    Exit gate = human peer review (NOT harness run-gate).
    A/B is serial per-deliverable: SRS → SPEC_TRACKING → TRACEABILITY.
    Each deliverable has its own A/B loop; REJECT only backtracks one step.
    """
    _ = repo_path  # reserved for future use (e.g. reading .methodology/state.json)
    lines = []
    lines.append("## Phase 1 Tasks: Requirements Specification")
    lines.append("")
    lines.append("### Phase 1 Overview")
    lines.append("Phase 1 is the project starting point. Define complete SRS.")
    lines.append("**Exit gate = human peer review of deliverables** (not `harness run-gate --gate 1`).")
    lines.append("")

    lines.append("> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.")
    lines.append("> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.")
    lines.append("")

    # P1 has exactly one checkpoint: human sign-off at phase end
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):")
    lines.append("> - CHECKPOINT-1: Human Peer Review (Phase 1 Exit) → `push-checkpoint --phase 1`")
    lines.append("")

    lines.extend(_preflight_steps(1))

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
            lines.append(f"#### {nfr['nfr']}: {nfr['title']}")
            lines.append(f"**Requirement**: {nfr['details'][:200]}")
            lines.append("")

    lines.append("### Phase 1 Deliverables")
    lines.append("- [ ] `SRS.md` - Software Requirements Specification (FRs + NFRs)")
    lines.append("- [ ] `CONSTRAINTS.md` - Technical constraints, SLA, cost model")
    lines.append("- [ ] `SPEC_TRACKING.md` - Spec tracking matrix")
    lines.append("- [ ] `TRACEABILITY_MATRIX.md` - Requirements traceability matrix")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("")

    lines.extend(_human_checkpoint(1, checkpoint_n=1))
    lines.extend(_phase_advance_step(1))
    return lines


def generate_phase2_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 2 detailed tasks (Architecture Design).

    Entry = P1 human APPROVE.  Exit gate = human peer review of SAD + ADR (NOT harness run-gate).
    """
    lines = []
    lines.append("## Phase 2 Tasks: Architecture Design")
    lines.append("")
    lines.append("### Phase 2 Overview")
    lines.append("Phase 2 designs the system architecture based on SRS, producing SAD and ADR.")
    lines.append("**Exit gate = human peer review of deliverables** (not `harness run-gate --gate 1`).")
    lines.append("")
    lines.append("> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.")
    lines.append("> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.")
    lines.append("")

    # P2 has exactly one checkpoint: human sign-off at phase end
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):")
    lines.append("> - CHECKPOINT-1: Human Peer Review (Phase 2 Exit) → `push-checkpoint --phase 2`")
    lines.append("")

    lines.extend(_entry_gate_check(2))  # confirm P1 human APPROVE
    lines.extend(_preflight_steps(2))

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
        "  python3 scripts/generate_sab.py --project $REPO",
        "  ```",
        "  - SAB.json contains: layers, modules, allowed_dependencies, quality_targets",
        "  - Used by: drift detector (M2), gate architecture dimension, constitution check",
        "  - Also embedded inline in `quality_manifest.json` via `harness_bridge`",
        "",
    ])
    lines.append("### Phase 2 Deliverables")
    lines.append("- [ ] `SAD.md` - Software Architecture Document (every FR has module mapping)")
    lines.append("- [ ] `ADR.md` - Architecture Decision Records")
    lines.append("- [ ] `ARCHITECTURE_DIAGRAM.md` - Architecture diagram")
    lines.append("- [ ] `.methodology/quality_manifest.json` — Quality manifest (FR list + SAB data)")
    lines.append("- [ ] `.methodology/SAB.json` — Machine-readable architecture baseline")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("")

    lines.extend(_human_checkpoint(2, checkpoint_n=1))
    lines.extend(_phase_advance_step(2))
    return lines


def generate_phase3_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 3 detailed tasks (Implementation + Gate 1 per-FR + Gate 2 exit)"""
    lines = []
    lines.append("## Phase 3 Tasks: Implementation")
    lines.append("")
    lines.append("### Phase 3 Overview")
    lines.append("Phase 3 implements all FR modules according to SAD, including unit tests.")
    lines.append("Each FR ends with a Gate 1 quality evaluation (CHECKPOINT). Phase exits via Gate 2.")
    lines.append("")

    frs = parse_srs_fr_sections(srs_path)
    modules = parse_sad_modules(repo_path)

    # Try manifest for definitive FR list
    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    fr_ids = manifest_fr_ids if manifest_fr_ids else [fr['fr'] for fr in frs]

    lines.extend(_checkpoint_index(fr_ids, phase=3))
    lines.extend(_entry_gate_check(3))
    lines.extend(_preflight_steps(3))

    if frs:
        lines.append("### FR Implementation Tasks ({} total)".format(len(frs)))
        lines.append("")
        checkpoint_n = 1
        for fr in frs:
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

            # A/B protocol then Gate 1
            lines.extend(_fr_dev_steps(fr['fr'], phase=3))
            lines.extend(_gate1_checkpoint(fr['fr'], phase=3, checkpoint_n=checkpoint_n))
            checkpoint_n += 1

    elif fr_ids:
        lines.append("### FR Implementation Tasks ({} total)".format(len(fr_ids)))
        lines.append("")
        checkpoint_n = 1
        for fr_id in fr_ids:
            lines.append(f"#### {fr_id}: [See SRS.md and SAD.md for implementation details]")
            lines.append("")
            lines.extend(_fr_dev_steps(fr_id, phase=3))
            lines.extend(_gate1_checkpoint(fr_id, phase=3, checkpoint_n=checkpoint_n))
            checkpoint_n += 1

    else:
        checkpoint_n = 1

    lines.extend(_p3_milestone_push_steps(fr_ids))

    lines.extend(_gate_exit_checkpoint(gate_num=2, phase=3, checkpoint_n=checkpoint_n))

    lines.append("### Phase 3 Deliverables")
    lines.append("- [ ] `03-development/src/` - All FR modules implemented")
    lines.append("- [ ] `tests/` - Unit tests (≥80% coverage per FR)")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("- [ ] Gate 2 PASS (phase exit, composite ≥ 75)")
    lines.append("")

    lines.extend(_phase_advance_step(3))
    return lines


def generate_phase4_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 4 detailed tasks (Testing + Gate 1 per-FR + Gate 3 exit)"""
    lines = []
    lines.append("## Phase 4 Tasks: Test Planning & Execution")
    lines.append("")
    lines.append("### Phase 4 Overview")
    lines.append("Phase 4 formulates and executes a complete test plan based on Phase 3 code.")
    lines.append("Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). Phase exits via Gate 3 (12 dims).")
    lines.append("")

    frs = parse_srs_fr_sections(srs_path)
    test_plans = parse_test_plan(repo_path)
    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    fr_ids = manifest_fr_ids if manifest_fr_ids else [fr['fr'] for fr in frs]

    lines.extend(_checkpoint_index(fr_ids, phase=4))
    lines.extend(_entry_gate_check(4))
    lines.extend(_preflight_steps(4))

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
                lines.extend(_gate1_checkpoint(fr_id, phase=4, checkpoint_n=checkpoint_n))
                checkpoint_n += 1
    elif frs:
        lines.append("### FR Test Coverage")
        lines.append("")
        for fr in frs:
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
            lines.extend(_gate1_checkpoint(fr['fr'], phase=4, checkpoint_n=checkpoint_n))
            checkpoint_n += 1
    elif fr_ids:
        lines.append("### FR Test Coverage ({} FRs)".format(len(fr_ids)))
        lines.append("")
        for fr_id in fr_ids:
            lines.append(f"#### {fr_id}: [See SRS.md for test targets]")
            lines.append("")
            lines.extend(_fr_dev_steps(fr_id, phase=4))
            lines.extend(_gate1_checkpoint(fr_id, phase=4, checkpoint_n=checkpoint_n))
            checkpoint_n += 1

    lines.extend(_gate_exit_checkpoint(gate_num=3, phase=4, checkpoint_n=checkpoint_n))

    lines.append("### Phase 4 Deliverables")
    lines.append("- [ ] `TEST_PLAN.md` - Test plan")
    lines.append("- [ ] `TEST_RESULTS.md` - Test results")
    lines.append("- [ ] `COVERAGE_REPORT.md` - Coverage report")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("- [ ] Gate 3 PASS (phase exit, composite ≥ 80, 12 dims)")
    lines.append("")

    lines.extend(_phase_advance_step(4))
    return lines


def generate_phase5_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 5 detailed tasks (Verification & Delivery + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 5 Tasks: Verification & Delivery")
    lines.append("")
    lines.append("### Phase 5 Overview")
    lines.append("Phase 5 verifies the system against test results, ensuring all FRs meet acceptance criteria.")
    lines.append("Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). No phase-exit gate — P5 was cleared by Gate 3 at P4 exit.")
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids, phase=5))
    lines.extend(_entry_gate_check(5))
    lines.extend(_preflight_steps(5))

    if manifest_fr_ids:
        lines.append("### FR Verification Tasks ({} total)".format(len(manifest_fr_ids)))
        lines.append("")
        checkpoint_n = 1
        for fr_id in manifest_fr_ids:
            lines.append(f"#### {fr_id}: Verification")
            lines.append(f"- [ ] Confirm all acceptance criteria from SRS.md are met for {fr_id}")
            lines.append(f"- [ ] Run integration tests for {fr_id}")
            lines.append("- [ ] Verify edge cases and error paths")
            lines.append("- [ ] Confirm ≥80% branch coverage")
            lines.append("")
            lines.extend(_fr_dev_steps(fr_id, phase=5))
            lines.extend(_gate1_checkpoint(fr_id, phase=5, checkpoint_n=checkpoint_n))
            checkpoint_n += 1
    else:
        lines.append("### Verification Items")
        lines.append("(No FR list found — add per-FR verification steps based on SRS.md)")
        lines.append("- [ ] Integration tests pass")
        lines.append("- [ ] Performance tests meet targets")
        lines.append("- [ ] Security scan passes")
        lines.append("- [ ] Baseline established")
        lines.append("")

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
    lines.append("- [ ] `BASELINE.md` - System baseline")
    lines.append("- [ ] `MONITORING_PLAN.md` - Monitoring plan")
    lines.append("- [ ] `VERIFICATION_REPORT.md` - Verification report")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("")

    lines.extend(_phase_advance_step(5))
    return lines


def generate_phase6_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 6 detailed tasks (Quality Assurance — Gate 4 full replacement)"""
    lines = []
    lines.append("## Phase 6 Tasks: Quality Assurance")
    lines.append("")
    lines.append("### Phase 6 Overview")
    lines.append("Phase 6 is a complete Gate 4 evaluation. Gate 4 replaces the entire P6 SOP.")
    lines.append("No FR loop — Gate 4 evaluates the full project (12 dims, CRG recon, Hermes APPROVE required).")
    lines.append("")

    # P6 has exactly one checkpoint: Gate 4
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint saved):")
    lines.append("> - CHECKPOINT-1: Gate 4 (Full Project — 12 dims, Hermes APPROVE)")
    lines.append("")

    lines.extend(_entry_gate_check(6))
    lines.extend(_preflight_steps(6))

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
    lines.append("- [ ] Confirm `HERMES_REVIEWER_TARGET` env var is set (e.g. `telegram:6308981865`)")
    lines.append("- [ ] Confirm `HERMES_TIMEOUT_MS=90000` is set")
    lines.append("")

    lines.extend(_gate_exit_checkpoint(gate_num=4, phase=6, checkpoint_n=1))

    lines.append("### Phase 6 Deliverables")
    lines.append("- [ ] Gate 4 PASS (composite ≥ 85, all 12 dims, CRG recon done)")
    lines.append("- [ ] Hermes APPROVE received from reviewer")
    lines.append("- [ ] `QUALITY_REPORT.md` - Quality report (auto-generated by Gate 4)")
    lines.append("- [ ] `RELEASE_NOTES.md` - Release notes")
    lines.append("- [ ] `FINAL_SIGN_OFF.md` - Final sign-off")
    lines.append("")

    lines.extend(_phase_advance_step(6))
    return lines


def generate_phase7_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 7 detailed tasks (Risk Management + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 7 Tasks: Risk Management")
    lines.append("")
    lines.append("### Phase 7 Overview")
    lines.append("Phase 7 identifies, tracks, and mitigates all risks introduced during development.")
    lines.append("Each FR gets a Gate 1 risk-aware re-evaluation (CHECKPOINT). No phase-exit gate — P7 cleared by Gate 4.")
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids, phase=7))
    lines.extend(_entry_gate_check(7))
    lines.extend(_preflight_steps(7))

    risks = parse_risk_register(repo_path)
    if risks:
        lines.append("### Risk Register ({} total)".format(len(risks)))
        lines.append("")
        for risk in risks:
            lines.append(f"- **{risk['name']}**: mitigation strategy required")
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
        checkpoint_n = 1
        for fr_id in manifest_fr_ids:
            lines.append(f"#### {fr_id}: Risk Assessment")
            lines.append(f"- [ ] Review open issues from previous gates for {fr_id}")
            lines.append(f"- [ ] Check `deferred_fixes.md` for {fr_id} entries")
            lines.append("- [ ] Confirm no new defects introduced")
            lines.append("")
            lines.extend(_fr_dev_steps(fr_id, phase=7))
            lines.extend(_gate1_checkpoint(fr_id, phase=7, checkpoint_n=checkpoint_n))
            checkpoint_n += 1
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
    lines.append("- [ ] `RISK_REGISTER.md` - Risk register")
    lines.append("- [ ] `RISK_MITIGATION_PLANS.md` - Mitigation plans")
    lines.append("- [ ] `RISK_STATUS_REPORT.md` - Risk status report")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("")

    lines.extend(_phase_advance_step(7))
    return lines


def generate_phase8_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 8 detailed tasks (Configuration Management + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 8 Tasks: Configuration Management")
    lines.append("")
    lines.append("### Phase 8 Overview")
    lines.append("Phase 8 establishes a complete configuration management system ensuring traceability.")
    lines.append("Each FR gets a Gate 1 config-aware re-evaluation (CHECKPOINT). No phase-exit gate — P8 cleared by Gate 4.")
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids, phase=8))
    lines.extend(_entry_gate_check(8))
    lines.extend(_preflight_steps(8))

    configs = parse_config_records(repo_path)
    if configs:
        lines.append("### Configuration Items ({} total)".format(len(configs)))
        lines.append("")
        for config in configs:
            lines.append(f"- **{config['name']}**: configuration record required")
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
        checkpoint_n = 1
        for fr_id in manifest_fr_ids:
            lines.append(f"#### {fr_id}: Configuration Record")
            lines.append(f"- [ ] Confirm {fr_id} configuration items are documented in CONFIG_RECORDS.md")
            lines.append("- [ ] Confirm environment variables / secrets are managed (not hardcoded)")
            lines.append(f"- [ ] Confirm deployment checklist entries for {fr_id}")
            lines.append("")
            lines.extend(_fr_dev_steps(fr_id, phase=8))
            lines.extend(_gate1_checkpoint(fr_id, phase=8, checkpoint_n=checkpoint_n))
            checkpoint_n += 1
    else:
        lines.append("(No FR list found in manifest — run Gate 1 per FR manually)")
        lines.append("")

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
    lines.append("- [ ] `DEPLOYMENT_CHECKLIST.md` - Deployment checklist")
    lines.append("- [ ] `ENVIRONMENT_SPEC.md` - Environment specification")
    lines.append("- [x] `sessions_spawn.log` — auto-populated by AgentSpawner (HR-10)")
    lines.append("- [ ] Gate 1 PASS for every FR")
    lines.append("")

    lines.extend(_phase_advance_step(8))
    return lines


# ============================================================================
# Main Generator
# ============================================================================

def generate_full_plan(phase: int, repo_path: Path, output_path: Optional[Path] = None) -> Optional[str]:
    """Generate full plan with phase-specific detailed tasks"""

    srs_paths = [
        repo_path / "01-requirements" / "SRS.md",
    ]
    srs_path = next((p for p in srs_paths if p.exists()), None)

    # Phase 2-4 need existing SRS; Phase 1 CREATES SRS, 5-8 use other artifacts
    if srs_path is None and phase in (2, 3, 4):
        print(f"[ERROR] SRS.md not found for phase {phase}")
        return None
    _srs = cast(Path, srs_path)  # safe: phases 1 and 5-8 don't use srs_path

    generators = {
        1: lambda: generate_phase1_tasks(repo_path, _srs),
        2: lambda: generate_phase2_tasks(repo_path, _srs),
        3: lambda: generate_phase3_tasks(repo_path, _srs),
        4: lambda: generate_phase4_tasks(repo_path, _srs),
        5: lambda: generate_phase5_tasks(repo_path),
        6: lambda: generate_phase6_tasks(repo_path),
        7: lambda: generate_phase7_tasks(repo_path),
        8: lambda: generate_phase8_tasks(repo_path),
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

    plan_lines = [
        f"# Phase {phase} Full Execution Plan -- {repo_path.name}",
        "",
        f"> **Version**: v{_HARNESS_VERSION} (project plan)",
        f"> **Project**: {repo_path.name}",
        f"> **Date**: {datetime.now().strftime('%Y-%m-%d')}",
        f"> **Framework**: harness-methodology v{_HARNESS_VERSION}",
        f"> **Phase**: {phase} - {phase_names.get(phase, 'Unknown')}",
        f"> **Status**: Full version (including Phase {phase} detailed tasks)",
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

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
from typing import Dict, List, Optional


# ============================================================================
# Phase-Specific Parsers
# ============================================================================

def parse_srs_fr_sections(srs_path: Path) -> List[Dict]:
    """Parse SRS.md to extract FR sections"""
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
            req_lines = [l.strip() for l in content_section.split('\n') if l.strip() and l.strip().startswith('-')]

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
        repo_path / "SAD.md",
        repo_path / "templates" / "SAD.md",
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
        repo_path / "TEST_PLAN.md",
        repo_path / "docs" / "TEST_PLAN.md",
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
        repo_path / "QUALITY_REPORT.md",
        repo_path / "docs" / "QUALITY_REPORT.md",
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
        repo_path / "RISK_REGISTER.md",
        repo_path / "docs" / "RISK_REGISTER.md",
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
        repo_path / "08-configuration" / "CONFIG_RECORDS.md",
        repo_path / "CONFIG_RECORDS.md",
        repo_path / "docs" / "CONFIG_RECORDS.md",
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


def parse_srs_nfr_sections(srs_path: Path) -> List[Dict]:
    """Parse SRS.md to extract NFR sections"""
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
# Phase Task Generators
# ============================================================================

def generate_phase1_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 1 detailed tasks (Requirements Specification)"""
    lines = []
    lines.append("## Phase 1 Tasks: Requirements Specification")
    lines.append("")
    lines.append("### Phase 1 Overview")
    lines.append("Phase 1 is the project starting point. Main task: define complete Software Requirements Specification (SRS).")
    lines.append("")

    frs = parse_srs_fr_sections(srs_path)
    nfrs = parse_srs_nfr_sections(srs_path)

    if frs:
        lines.append("### FR Requirements ({} total)".format(len(frs)))
        lines.append("")
        for fr in frs:
            lines.append(f"#### {fr['fr']}: {fr['title']}")
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
    lines.append("- [ ] `SRS.md` - Software Requirements Specification")
    lines.append("- [ ] `SPEC_TRACKING.md` - Spec Tracking Matrix")
    lines.append("- [ ] `TRACEABILITY_MATRIX.md` - Requirements Traceability Matrix")
    lines.append("")

    return lines


def generate_phase2_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 2 detailed tasks (Architecture Design)"""
    lines = []
    lines.append("## Phase 2 Tasks: Architecture Design")
    lines.append("")
    lines.append("### Phase 2 Overview")
    lines.append("Phase 2 designs the system architecture based on SRS, producing SAD and ADR.")
    lines.append("")

    frs = parse_srs_fr_sections(srs_path)
    modules = parse_sad_modules(repo_path)

    if frs:
        lines.append("### FR Architecture Mapping ({} total)".format(len(frs)))
        lines.append("")
        for fr in frs:
            lines.append(f"#### {fr['fr']}: {fr['title']}")
            lines.append(f"**Requirement**: {fr['desc']}")

            mod = modules.get(fr['fr'], {})
            if mod:
                lines.append("**Module Mapping**:")
                lines.append(f"- Module: `{mod.get('module', 'N/A')}`")
                lines.append(f"- File:   `{mod.get('file', 'N/A')}`")
            lines.append("")

    lines.append("### Phase 2 Deliverables")
    lines.append("- [ ] `SAD.md` - Software Architecture Document")
    lines.append("- [ ] `ADR.md` - Architecture Decision Records")
    lines.append("- [ ] `ARCHITECTURE_DIAGRAM.md` - Architecture diagram")
    lines.append("")

    return lines


def generate_phase3_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 3 detailed tasks (Implementation)"""
    lines = []
    lines.append("## Phase 3 Tasks: Implementation")
    lines.append("")
    lines.append("### Phase 3 Overview")
    lines.append("Phase 3 implements all FR modules according to SAD, including unit tests.")
    lines.append("")

    frs = parse_srs_fr_sections(srs_path)
    modules = parse_sad_modules(repo_path)

    if frs:
        lines.append("### FR Implementation Tasks ({} total)".format(len(frs)))
        lines.append("")
        for fr in frs:
            lines.append(f"#### {fr['fr']}: {fr['title']}")
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

    lines.append("### Phase 3 Deliverables")
    lines.append("- [ ] `03-development/src/processing/` - Processing modules")
    lines.append("- [ ] `03-development/src/synth/` - Synthesis modules")
    lines.append("- [ ] `03-development/src/infrastructure/` - Infrastructure modules")
    lines.append("- [ ] `03-development/src/api/` - API routes")
    lines.append("- [ ] `tests/` - Unit tests")
    lines.append("")

    return lines


def generate_phase4_tasks(repo_path: Path, srs_path: Path) -> List[str]:
    """Generate Phase 4 detailed tasks (Testing)"""
    lines = []
    lines.append("## Phase 4 Tasks: Test Planning & Execution")
    lines.append("")
    lines.append("### Phase 4 Overview")
    lines.append("Phase 4 formulates and executes a complete test plan based on Phase 3 code.")
    lines.append("")

    frs = parse_srs_fr_sections(srs_path)
    test_plans = parse_test_plan(repo_path)

    if test_plans:
        lines.append("### Test Items ({} total)".format(len(test_plans)))
        lines.append("")
        for tp in test_plans:
            lines.append(f"#### {tp['title']}")
            lines.append(f"**Content**: {tp['details'][:200]}")
            lines.append("")
    else:
        lines.append("### FR Test Coverage")
        lines.append("")
        for fr in frs:
            lines.append(f"#### {fr['fr']}: {fr['title']}")
            lines.append(f"**Test Target**: Verify {fr['desc']}")
            if fr['test_cases']:
                lines.append("**Test Cases**:")
                for inp, out in fr['test_cases']:
                    lines.append(f"- Input [{inp}] -> Output [{out}]")
            lines.append("")

    lines.append("### Phase 4 Deliverables")
    lines.append("- [ ] `TEST_PLAN.md` - Test plan")
    lines.append("- [ ] `TEST_RESULTS.md` - Test results")
    lines.append("- [ ] `COVERAGE_REPORT.md` - Coverage report")
    lines.append("")

    return lines


def generate_phase5_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 5 detailed tasks (Verification & Delivery)"""
    lines = []
    lines.append("## Phase 5 Tasks: Verification & Delivery")
    lines.append("")
    lines.append("### Phase 5 Overview")
    lines.append("Phase 5 verifies the system against test results, ensuring requirements are met.")
    lines.append("")

    lines.append("### Verification Items")
    lines.append("- [ ] Integration tests pass")
    lines.append("- [ ] Performance tests meet targets")
    lines.append("- [ ] Security scan passes")
    lines.append("- [ ] Baseline established")
    lines.append("")

    lines.append("### Phase 5 Deliverables")
    lines.append("- [ ] `BASELINE.md` - System baseline")
    lines.append("- [ ] `MONITORING_PLAN.md` - Monitoring plan")
    lines.append("- [ ] `VERIFICATION_REPORT.md` - Verification report")
    lines.append("")

    return lines


def generate_phase6_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 6 detailed tasks (Quality Assurance)"""
    lines = []
    lines.append("## Phase 6 Tasks: Quality Assurance")
    lines.append("")
    lines.append("### Phase 6 Overview")
    lines.append("Phase 6 performs comprehensive quality assessment to ensure system meets release standards.")
    lines.append("")

    qr = parse_quality_report(repo_path)
    if qr.get('metrics'):
        lines.append("### Quality Metrics")
        lines.append("")
        for metric, value in qr['metrics']:
            lines.append(f"- **{metric}**: {value}")
        lines.append("")

    lines.append("### Phase 6 Deliverables")
    lines.append("- [ ] `QUALITY_REPORT.md` - Quality report")
    lines.append("- [ ] `RELEASE_NOTES.md` - Release notes")
    lines.append("- [ ] `FINAL_SIGN_OFF.md` - Final sign-off")
    lines.append("")

    return lines


def generate_phase7_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 7 detailed tasks (Risk Management)"""
    lines = []
    lines.append("## Phase 7 Tasks: Risk Management")
    lines.append("")
    lines.append("### Phase 7 Overview")
    lines.append("Phase 7 identifies, tracks, and mitigates all identified risks.")
    lines.append("")

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

    lines.append("### Phase 7 Deliverables")
    lines.append("- [ ] `RISK_REGISTER.md` - Risk register")
    lines.append("- [ ] `RISK_MITIGATION_PLANS.md` - Mitigation plans")
    lines.append("- [ ] `RISK_STATUS_REPORT.md` - Risk status report")
    lines.append("")

    return lines


def generate_phase8_tasks(repo_path: Path) -> List[str]:
    """Generate Phase 8 detailed tasks (Configuration Management)"""
    lines = []
    lines.append("## Phase 8 Tasks: Configuration Management")
    lines.append("")
    lines.append("### Phase 8 Overview")
    lines.append("Phase 8 establishes a complete configuration management system ensuring traceability.")
    lines.append("")

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

    lines.append("### Phase 8 Deliverables")
    lines.append("- [ ] `CONFIG_RECORDS.md` - Configuration records")
    lines.append("- [ ] `DEPLOYMENT_CHECKLIST.md` - Deployment checklist")
    lines.append("- [ ] `ENVIRONMENT_SPEC.md` - Environment specification")
    lines.append("")

    return lines


# ============================================================================
# Main Generator
# ============================================================================

def generate_full_plan(phase: int, repo_path: Path, output_path: Path = None) -> Optional[str]:
    """Generate full plan with phase-specific detailed tasks"""

    srs_paths = [
        repo_path / "SRS.md",
        repo_path / "01-requirements" / "SRS.md",
        repo_path / "docs" / "SRS.md",
    ]
    srs_path = next((p for p in srs_paths if p.exists()), None)

    generators = {
        1: lambda: generate_phase1_tasks(repo_path, srs_path),
        2: lambda: generate_phase2_tasks(repo_path, srs_path),
        3: lambda: generate_phase3_tasks(repo_path, srs_path),
        4: lambda: generate_phase4_tasks(repo_path, srs_path),
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
        f"> **Version**: v6.49.0",
        f"> **Project**: {repo_path.name}",
        f"> **Date**: {datetime.now().strftime('%Y-%m-%d')}",
        f"> **Framework**: harness-methodology v6.49.0",
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

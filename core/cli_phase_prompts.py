# Phase Prompts - All 8 Phases
# This module contains Phase-specific prompts for plan-phase generation

PHASE_PROMPTS = {
    1: {
        "name": "Requirements Specification",
        "agent_a": "requirements_engineer",
        "agent_b": "business_analyst",
        "developer": """```
TASK: Draft Software Requirements Specification (SRS)
TASK_ID: task-p1
═══════════════════════════════════════

[Phase Goal]
Establish complete Software Requirements Specification (SRS) covering functional requirements (FR) and non-functional requirements (NFR)

[On Demand Reading] (read only these sections, NO full-file dump)
- TASK_INITIALIZATION_PROMPT.md (read only project goals and constraints)

[Outputs]
- SRS.md: Software Requirements Specification document
- SPEC_TRACKING.md: Specification Tracking Matrix
- TRACEABILITY_MATRIX.md: Requirements Traceability Matrix

[Verification Criteria]
- Constitution SRS ≥80%
- Every FR has explicit acceptance criteria
- Every NFR traceable to an FR
- Traceability matrix 100% complete

[FORBIDDEN]
- NO missing FR or NFR
- NO vague or unverifiable specs
- NO missing interface specs
- NO citations missing or lacking line numbers -> HR-15 violation

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (SRS.md path)",
 "confidence": 1-10,
 "citations": ["TASK_INITIALIZATION_PROMPT.md#L10-L20"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review Software Requirements Specification (SRS)
TASK_ID: task-p1-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- SRS.md (read only FR and NFR sections)
- SPEC_TRACKING.md
- TRACEABILITY_MATRIX.md

[Verification Checklist]
1. Every FR has explicit acceptance criteria
2. Every NFR traceable to a corresponding FR
3. Traceability matrix complete (FR -> NFR -> Test)
4. Constitution SRS score >=80%
5. Interface specs clear (input/output/error handling)

[REJECT_IF]
- FR missing or vague -> REJECT
- NFR unverifiable -> REJECT
- Traceability incomplete -> REJECT
- ❌ Constitution < 80% → REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "constitution_score": "score",
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    2: {
        "name": "Architecture Design",
        "agent_a": "architect",
        "agent_b": "tech_lead",
        "developer": """```
TASK: Draft System Architecture Document (SAD) + Architecture Decision Records (ADR)
TASK_ID: task-p2
═══════════════════════════════════════

[Phase Goal]
Design system architecture based on SRS, covering module boundaries, interfaces, data flow

[On Demand Reading] (read only these sections, NO full-file dump)
- SRS.md (read only FR requirements and interface specs)
- Task initialization prompt (read only constraints)

[Outputs]
- SAD.md: System Architecture Document
- ADR.md: Architecture Decision Records (one entry per key decision)

[Verification Criteria]
- Constitution SAD ≥80%
- SAD<->SRS consistency =100%
- Every FR has a corresponding Module
- Every Module has clear responsibilities and interfaces
- ADR.md `<!-- harness:template-stub -->` sentinel removed before submission
- `check-constitution --phase 2 --file 02-architecture/adr/ADR.md` returns PASS

[FORBIDDEN]
- NO deviations from SRS requirements
- NO vague or overlapping module boundaries
- NO missing error-handling mechanism
- NO citations missing or lacking line numbers -> HR-15 violation

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (SAD.md, ADR.md path)",
 "confidence": 1-10,
 "citations": ["SRS.md#L20-L30", "SAD.md#L10-L15"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review System Architecture Document (SAD) + Architecture Decision Records (ADR)
TASK_ID: task-p2-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- SAD.md (read only Module boundary and interface sections)
- ADR.md (read only decision rationale)
- SRS.md (read only FR-corresponding sections)

[Verification Checklist]
1. SAD<->SRS consistency =100% (every FR has a corresponding Module)
2. Every Module has clear responsibility (Single Responsibility Principle)
3. Interface specs clear (input/output/error handling)
4. ADR records key decisions and their rationale
5. Constitution SAD score >=80%

[REJECT_IF]
- SAD<->SRS inconsistent -> REJECT
- Module responsibilities overlap -> REJECT
- Interface vague -> REJECT
- ADR decision rationale insufficient -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "consistency_score": "SRS<->SAD consistency %",
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    3: {
        "name": "Code Implementation",
        "agent_a": "developer",
        "agent_b": "reviewer",
        "developer": """```
TASK: {fr['fr']} {fr['title']}
TASK_ID: task-{fr_num}
═══════════════════════════════════════

[Prerequisites]
cd {repo_path}
pwd  # confirm in correct directory

[Phase Hooks Integration] (HR-09 enforced)
IMPORTANT: PhaseHooksAdapter must be called before and after each FR:
import sys
from pathlib import Path
sys.path.insert(0, '{methodology_path}')
from adapters.phase_hooks_adapter import PhaseHooksAdapter

adapter = PhaseHooksAdapter(
    project_path="{repo_path}",
    phase={phase_num},
    feature_flags={{"uqlm": True, "gap_detector": True, "hunter": True, "shields": True}}
)
# Preflight: run once before first FR only
adapter.preflight_all()

# Before FR
adapter.monitoring_before_dev("{fr_id}")

# Developer implements...

# After FR (pass developer result in)
hook_result = adapter.monitoring_after_dev("{fr_id}", result=dev_result)
if not hook_result.get("passed"):
    raise Exception(f"Hook blocked by Feature: {{hook_result}}")

[Phase Goal]
Implement specified module per SAD, including unit tests

[On Demand Reading] (read only these sections, NO full-file dump)

Read from SRS.md only:
- §{fr['fr']} requirement description
- §{fr['fr']} test cases (if any)

Read from SAD.md only:
- §Module boundary table (section corresponding to {fr['fr']})

[Outputs]
- {fr.get('file', 'app/processing/{fr_num}.py')}: implementation code
- tests/test_{fr_num}.py: unit tests

[Verification Criteria]
- pytest 100% pass
- coverage >=70%
- docstring includes [FR-XX] tag
- docstring includes Citations (SRS.md#Llinenum, SAD.md#Llinenum)

[FORBIDDEN]
- NO dumping full SRS.md/SAD.md
- NO app/infrastructure/ (deprecated, use correct directory)
- NO missing [FR-XX] tag in docstring
- NO missing Citations (with line numbers) in docstring
- ❌ @type: edge
- NO ellipsis omissions -> task failure
- NO citations missing or lacking line numbers -> HR-15 violation
- NO citations omitted from code docstring -> HR-15 violation
- NO skipping PhaseHooks call -> HR-09 violation

[OUTPUT_FORMAT]
{{
  "status": "success|error|unable_to_proceed",
  "result": "actual output (path)",
  "confidence": 1-10,
  "citations": ["{fr['fr']}", "SRS.md#L23-L45", "SAD.md#L50-L60"],
  "hook_calls": {{
    "monitoring_before_dev": {{ "blocked": False }},
    "monitoring_after_dev": {{
      "passed": True,
      "shield_verdict": "{{shield_verdict}}",
      "uqlm_score": {{uqlm_score}},
      "hunter_severity": null
    }}
  }},
  "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
HR-09 enforced: Phase Hooks must be called for every FR
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review {fr['fr']} {fr['title']}
TASK_ID: task-{fr_num}-review
═══════════════════════════════════════

[Prerequisites]
cd {repo_path}

[Phase Hooks Integration]
# Before Reviewer runs
adapter.monitoring_before_rev("{fr_id}")

[Review Scope] (read only these sections, NO full-file dump)

Files to review:
- {fr.get('file', 'app/processing/{fr_num}.py')} (every function docstring must include [FR-XX])
- tests/test_{fr_num}.py

Spec reference:
- SRS.md §{fr['fr']} (read only requirement and test case sections)

[Verification Checklist]
1. Every public function docstring includes [FR-XX] tag
2. Every public function docstring includes Citations (SRS.md#Llinenum, SAD.md#Llinenum)
3. Test coverage >=70%
4. pytest 100% pass
5. No logic errors or security vulnerabilities
6. Constitution maintainability >90% (TH-05)

[REJECT_IF]
- docstring missing [FR-XX] tag -> REJECT
- docstring missing Citations (with line numbers) -> REJECT
- NFR constraint violated -> REJECT
- ❌ confidence < 6 → REJECT
- Missing citations or citations lacking line numbers -> REJECT (HR-15)
- coverage < 70% -> REJECT
- NO skipping PhaseHooks call -> HR-09 violation

# After Reviewer finishes
hook_result = adapter.monitoring_after_rev("{fr_id}", result=rev_result)

[OUTPUT_FORMAT]
{{
  "status": "APPROVE|REJECT",
  "confidence": 1-10,
  "violations": ["specific issues"],
  "coverage": "coverage %",
  "hook_calls": {{
    "monitoring_before_rev": {{ "blocked": False }},
    "monitoring_after_rev": {{ "passed": True }}
  }},
  "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    4: {
        "name": "Testing Planning and Execution",
        "agent_a": "qa",
        "agent_b": "reviewer",
        "developer": """```
TASK: Draft Test Plan (TEST_PLAN) + Execute Tests (TEST_RESULTS)
TASK_ID: task-p4
═══════════════════════════════════════

[Phase Goal]
Develop complete test plan based on Phase 3 code and execute

[On Demand Reading] (read only these sections, NO full-file dump)
- SRS.md (read only FR requirements and acceptance criteria)
- SAD.md (read only Module interfaces)
- src/ (view only exported public interfaces)

[Outputs]
- TEST_PLAN.md: Test Plan (test strategy, environment, risks)
- TEST_RESULTS.md: Test Results (execution log, pass rate)
- COVERAGE_REPORT.md: Coverage Report

[Verification Criteria]
- Constitution test coverage >90% (TH-06)
- FR<->test mapping rate >=90%
- Integration tests 100% pass
- Performance tests pass (if applicable)

[FORBIDDEN]
- NO test cases without FR mapping
- NO un-isolated test environment
- NO uncovered critical paths
- NO citations missing or lacking line numbers -> HR-15 violation

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (TEST_PLAN.md, TEST_RESULTS.md path)",
 "confidence": 1-10,
 "citations": ["SRS.md#L20-L30", "src/"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review Test Plan (TEST_PLAN) + Test Results (TEST_RESULTS)
TASK_ID: task-p4-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- TEST_PLAN.md
- TEST_RESULTS.md
- COVERAGE_REPORT.md

[Verification Checklist]
1. Every FR has corresponding test cases
2. FR<->test mapping rate >=90%
3. Critical paths fully covered
4. Test environment matches production environment
5. Constitution test score >80%

[REJECT_IF]
- FR not fully covered -> REJECT
- Critical paths untested -> REJECT
- Environment inconsistent -> REJECT
- coverage < 80% -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "coverage_rate": "FR<->test mapping rate %",
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    5: {
        "name": "Verification and Delivery",
        "agent_a": "devops",
        "agent_b": "architect",
        "developer": """```
TASK: Establish System Baseline + Monitoring Plan
TASK_ID: task-p5
═══════════════════════════════════════

[Phase Goal]
Establish system Baseline based on test results, ensuring monitorability and traceability

[On Demand Reading] (read only these sections, NO full-file dump)
- TEST_RESULTS.md (read only pass/fail statistics)
- SRS.md (read only performance requirements and constraints)

[Outputs]
- BASELINE.md: System Baseline (performance benchmarks, configuration snapshot)
- VERIFICATION_REPORT.md: Verification Report

[Verification Criteria]
- Baseline performance meets SRS constraints
- Monitoring covers key metrics
- Alert thresholds set reasonably

[FORBIDDEN]
- NO Baseline deviating from actual performance
- NO missing key monitoring metrics
- NO alert thresholds too broad or too strict
- NO citations missing or lacking line numbers -> HR-15 violation

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (BASELINE.md, VERIFICATION_REPORT.md path)",
 "confidence": 1-10,
 "citations": ["TEST_RESULTS.md#L10-L20"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review System Baseline + Verification Report
TASK_ID: task-p5-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- BASELINE.md
- TEST_RESULTS.md (read only statistics)

[Verification Checklist]
1. Baseline performance meets SRS constraints
2. Monitoring covers all key metrics
3. Alert thresholds reasonable (achievable, no false positives)
4. Monitoring dashboard is trackable
5. Constitution verification score >=80%

[REJECT_IF]
- Baseline does not meet SRS -> REJECT
- Monitoring metrics missing -> REJECT
- Alert thresholds unreasonable -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    6: {
        "name": "Quality Assurance",
        "agent_a": "qa",
        "agent_b": "architect",
        "developer": """```
TASK: Generate Quality Report (QUALITY_REPORT)
TASK_ID: task-p6
═══════════════════════════════════════

[Phase Goal]
Conduct comprehensive quality assessment, ensuring system meets release standards

[On Demand Reading] (read only these sections, NO full-file dump)
- TEST_RESULTS.md (read only failure cases)
- BASELINE.md (read only performance data)
- QUALITY_REPORT.md (read existing version if any)

[Outputs]
- QUALITY_REPORT.md: Quality Report (quality dimensions, metrics, issue list)
- Issue remediation plan (if issues exist)

[Verification Criteria]
- Constitution quality total score >=80%
- Logic correctness score >=90
- All high-priority issues fixed or risk-accepted
- QUALITY_REPORT.md MUST cite Phase 5 outputs by filename: BASELINE.md and
  VERIFICATION_REPORT.md (ASPICE SWE traceability — postflight enforced)

[FORBIDDEN]
- NO concealing quality issues
- NO high-priority issues unresolved
- NO data in report inconsistent with actuals
- NO citations missing or lacking line numbers -> HR-15 violation
- NO QUALITY_REPORT.md without explicit reference to BASELINE and VERIFICATION_REPORT

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (QUALITY_REPORT.md path)",
 "confidence": 1-10,
 "citations": ["TEST_RESULTS.md#L30-L40"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review Quality Report (QUALITY_REPORT)
TASK_ID: task-p6-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- QUALITY_REPORT.md
- TEST_RESULTS.md
- BASELINE.md

[Verification Checklist]
1. Constitution quality total score >=80%
2. Logic correctness score >=90
3. High-priority issues fixed or risk-accepted
4. Quality trend reasonable (compared to Baseline)
5. Release recommendation is clear

[REJECT_IF]
- ❌ Constitution < 80% → REJECT
- NO high-priority issues unresolved → REJECT
- Data inconsistent with actuals -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)
- ❌ QUALITY_REPORT.md does not mention BASELINE or VERIFICATION_REPORT by filename → REJECT
  (ASPICE traceability — postflight will block gate finalization if missing)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "quality_score": "Constitution score",
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    7: {
        "name": "Risk Management",
        "agent_a": "qa",
        "agent_b": "pm",
        "developer": """```
TASK: Risk Identification, Assessment and Mitigation Plan
TASK_ID: task-p7
═══════════════════════════════════════

[Phase Goal]
Identify, track and develop mitigation strategies for all identified risks

[On Demand Reading] (read only these sections, NO full-file dump)
- QUALITY_REPORT.md (read only issues and risk sections)
- SRS.md (read only constraints and assumptions)

[Outputs]
- RISK_REGISTER.md: Risk Register (risk description, probability, impact, status)
- RISK_MITIGATION_PLANS.md: Mitigation Plans (response strategy for each risk)
- RISK_STATUS_REPORT.md: Risk Status Report

[Verification Criteria]
- All identified risks have mitigation plans
- Risk status correct (Open/InProgress/Closed)
- Mitigation plans feasible with assigned responsibility

[FORBIDDEN]
- NO missing known risks
- NO non-objective risk assessment
- NO vague or infeasible mitigation plans
- NO citations missing or lacking line numbers -> HR-15 violation

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (RISK_REGISTER.md path)",
 "confidence": 1-10,
 "citations": ["QUALITY_REPORT.md#L20-L30"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review Risk Management Documents
TASK_ID: task-p7-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- RISK_REGISTER.md
- RISK_MITIGATION_PLANS.md
- QUALITY_REPORT.md

[Verification Checklist]
1. All identified risks have corresponding mitigation plans
2. Risk assessment reasonable (probability x impact)
3. Mitigation plans specific and feasible
4. Responsibility assignment clear
5. Tracking mechanism in place

[REJECT_IF]
- Risk missing -> REJECT
- Assessment not objective -> REJECT
- Mitigation plan infeasible -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "risk_count": "number of risks",
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    },
    
    8: {
        "name": "Configuration Management",
        "agent_a": "devops",
        "agent_b": "pm",
        "developer": """```
TASK: Establish Configuration Management System, ensure traceability
TASK_ID: task-p8
═══════════════════════════════════════

[Phase Goal]
Establish complete configuration management system, ensuring deployability and reproducibility

[On Demand Reading] (read only these sections, NO full-file dump)
- RISK_REGISTER.md (read only known risks)
- BASELINE.md (read only configuration snapshot)
- QUALITY_REPORT.md (if available)

[Outputs]
- CONFIG_RECORDS.md: Configuration Records (environment, version, parameters)
- RELEASE_CHECKLIST.md: Release Checklist

[Verification Criteria]
- RELEASE_CHECKLIST.md exists and complete
- Deployment checklist 100% executable
- Configuration records traceable to every component

[FORBIDDEN]
- NO RELEASE_CHECKLIST.md inconsistent with actuals
- NO incomplete deployment checklist
- NO missing key parameters in configuration records
- NO citations missing or lacking line numbers -> HR-15 violation

[OUTPUT_FORMAT]
{{
 "status": "success|error|unable_to_proceed",
 "result": "actual output (CONFIG_RECORDS.md, RELEASE_CHECKLIST.md path)",
 "confidence": 1-10,
 "citations": ["BASELINE.md#L10-L15"],
 "summary": "within 50 words"
}}

HR-15 enforced: citations must include 'filename#Llinenum' format
═══════════════════════════════════════
```""",
        "reviewer": """```
TASK: Review Configuration Management Documents
TASK_ID: task-p8-review
═══════════════════════════════════════

[Review Scope] (read only these sections, NO full-file dump)
- CONFIG_RECORDS.md
- RELEASE_CHECKLIST.md

[Verification Checklist]
1. RELEASE_CHECKLIST.md fully consistent with actuals
2. Deployment checklist fully executable
3. Configuration records cover all environments (Dev/Staging/Prod)
4. Version consistency (component versions, dependency versions)
5. Deployment process reproducible

[REJECT_IF]
- RELEASE_CHECKLIST.md incomplete or inconsistent -> REJECT
- Deployment checklist incomplete -> REJECT
- Configuration missing key parameters -> REJECT
- Missing citations or no line numbers -> REJECT (HR-15)

[OUTPUT_FORMAT]
{{
 "status": "APPROVE|REJECT",
 "confidence": 1-10,
 "violations": ["specific issues"],
 "deployment_ready": true/false,
 "summary": "within 50 words"
}}
═══════════════════════════════════════
```"""
    }
}

# Helper function to get prompts for a phase
def get_phase_prompts(phase: int) -> dict:
    """Get developer and reviewer prompts for a phase"""
    return PHASE_PROMPTS.get(phase, PHASE_PROMPTS[3])

# Helper function to get role for a phase
def get_phase_role(phase: int, is_agent_a: bool = True) -> str:
    """Get agent role for a phase"""
    prompts = PHASE_PROMPTS.get(phase, PHASE_PROMPTS[3])
    return prompts["agent_a"] if is_agent_a else prompts["agent_b"]

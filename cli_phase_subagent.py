# Phase Sub-Agent Management - All 8 Phases
# Includes: Need-to-Know, On-Demand, Tool Timing, Context Isolation

PHASE_SUBAGENT = {
    1: {
        "name": "Requirements Specification",
        "agent_a": {"role": "architect", "task": "Develop SRS"},
        "agent_b": {"role": "reviewer", "task": "Review SRS"},
        
        # Need-to-Know: provide only information necessary for this phase
        "need_to_know": {
            "read": [
                {"path": "TASK_INITIALIZATION_PROMPT.md", "section": "Project Goals and Constraints", "why": "need to know project scope"}
            ],
            "skip": ["SRS.md", "SAD.md", "Phase 3-8 outputs"],
            "context": "single_phase"  # focus on Phase 1 only
        },
        
        # On-Demand: request additional information only when needed
        "on_demand": {
            "trigger": "When existing information is insufficient to define FRs",
            "request_to": "Johnny",
            "format": "List missing information (interface/performance/constraints)"
        },
        
        # Tool timing: when to use which tools
        "tool_timing": {
            "spawn": {
                "when": "Dispatch architect at the start",
                "tool": "sessions_spawn",
                "params": {"role": "architect", "fresh_messages": []}
            },
            "knowledge_curator": {
                "when": "Before dispatch",
                "tool": "KnowledgeCurator.verify_coverage",
                "check": "FR coverage >= 80%"
            },
            "context_manager": {
                "when": "Messages > 50",
                "tool": "ContextManager.compress",
                "level": "L1"
            },
            "checkpoint": {
                "when": "After each FR is complete",
                "tool": "SessionManager.save",
                "path": ".methodology/checkpoints/p1-{fr}.json"
            }
        },
        
        # sessions_spawn isolation
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],  # clean context
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    },
    
    2: {
        "name": "Architecture Design",
        "agent_a": {"role": "architect", "task": "Develop SAD+ADR"},
        "agent_b": {"role": "reviewer", "task": "Review SAD"},
        
        "need_to_know": {
            "read": [
                {"path": "SRS.md", "section": "FR requirements and interface specs", "why": "need to map each FR to a Module"},
                {"path": "TASK_INITIALIZATION_PROMPT.md", "section": "Constraints", "why": "architecture must not violate constraints"}
            ],
            "skip": ["Phase 3-8 outputs", "full SRS.md"],
            "context": "single_phase"
        },
        
        "on_demand": {
            "trigger": "When a SAD module cannot be mapped to an SRS FR",
            "request_to": "Johnny or revert to Phase 1 for correction",
            "format": "Flag which FR is missing or needs to be added"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "After SRS Phase APPROVE",
                "tool": "sessions_spawn",
                "params": {"role": "architect", "fresh_messages": []}
            },
            "knowledge_curator": {
                "when": "Before dispatch",
                "tool": "KnowledgeCurator.verify_coverage",
                "check": "SRS -> SAD trace complete"
            },
            "context_manager": {
                "when": "Messages > 50",
                "tool": "ContextManager.compress",
                "level": "L1"
            },
            "quality_gate": {
                "when": "After SAD is complete",
                "tool": "sad_constitution_checker",
                "threshold": "≥80%"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    },
    
    3: {
        "name": "Code Implementation",
        "agent_a": {"role": "developer", "task": "Implement FR-XX"},
        "agent_b": {"role": "reviewer", "task": "Review FR-XX code"},
        
        "need_to_know": {
            "read": [
                {"path": "SRS.md", "section": "§FR-XX requirement description", "why": "implement only this FR's functionality"},
                {"path": "SAD.md", "section": "§Module boundary map", "why": "know Module interface and boundary"}
            ],
            "skip": ["full SRS.md", "full SAD.md", "other FRs' implementation"],
            "context": "single_fr"  # isolate each FR
        },
        
        "on_demand": {
            "trigger": "When implementation details of other FRs are needed",
            "request_to": "N/A (should not occur; each FR is independent)",
            "format": "Return error: Need-to-Know violation"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "Dispatch a separate developer for each FR",
                "tool": "sessions_spawn",
                "params": {"role": "developer", "fr_id": "{fr}"}
            },
            "parallel": {
                "when": "When multiple FRs have no dependencies",
                "tool": "SubagentIsolator.parallel_spawn",
                "max_parallel": 3
            },
            "knowledge_curator": {
                "when": "Before dispatch",
                "tool": "KnowledgeCurator.verify_coverage",
                "check": "FR has been understood"
            },
            "context_manager": {
                "when": "Messages > 30",  # lowered because each FR is independent
                "tool": "ContextManager.compress",
                "level": "L1"
            },
            "test_runner": {
                "when": "After code is complete",
                "tool": "pytest",
                "params": {"path": "tests/test_{fr}.py", "cov": "≥70%"}
            },
            "quality_gate": {
                "when": "After Reviewer APPROVE",
                "tool": "stage_pass",
                "check": "commit + push"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": ["SRS.md §FR-XX", "SAD.md §Module"],
            "log_format": '{"timestamp","role","task","session_id","fr","confidence","commit"}'
        }
    },
    
    4: {
        "name": "Testing & Execution",
        "agent_a": {"role": "qa", "task": "Develop TEST_PLAN + Execute"},
        "agent_b": {"role": "reviewer", "task": "Review tests"},
        
        "need_to_know": {
            "read": [
                {"path": "SRS.md", "section": "FR requirements and acceptance criteria", "why": "test cases must correspond to FRs"},
                {"path": "SAD.md", "section": "Module interfaces", "why": "test interface boundaries"},
                {"path": "src/", "section": "Exported public interfaces", "why": "know what to test"}
            ],
            "skip": ["Phase 5-8 outputs", "full codebase"],
            "context": "single_phase"
        },
        
        "on_demand": {
            "trigger": "When implementation details for specific unit tests are needed",
            "request_to": "Revert to Phase 3 developer",
            "format": "Request via GitHub commit"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "After all Phase 3 APPROVEs",
                "tool": "sessions_spawn",
                "params": {"role": "qa"}
            },
            "test_runner": {
                "when": "After TEST_PLAN is complete",
                "tool": "pytest",
                "params": {"markers": "integration"}
            },
            "coverage": {
                "when": "After tests are executed",
                "tool": "CoverageReport",
                "check": "FR <-> test mapping rate >= 90%"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    },
    
    5: {
        "name": "Verification & Delivery",
        "agent_a": {"role": "devops", "task": "Establish Baseline + Monitoring"},
        "agent_b": {"role": "architect", "task": "Review Baseline"},
        
        "need_to_know": {
            "read": [
                {"path": "TEST_RESULTS.md", "section": "Pass/Fail statistics", "why": "establish performance baseline"},
                {"path": "SRS.md", "section": "Performance requirements and constraints", "why": "ensure Baseline meets requirements"}
            ],
            "skip": ["Phase 6-8 outputs", "detailed test cases"],
            "context": "single_phase"
        },
        
        "on_demand": {
            "trigger": "When test results do not meet SRS performance constraints",
            "request_to": "Revert to Phase 3/4 for correction",
            "format": "Create Issue for tracking"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "After Phase 4 APPROVE",
                "tool": "sessions_spawn",
                "params": {"role": "devops"}
            },
            "monitoring": {
                "when": "After Baseline is established",
                "tool": "setup_monitoring",
                "check": "Alert thresholds are reasonable"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    },
    
    6: {
        "name": "Quality Assurance",
        "agent_a": {"role": "qa", "task": "Generate QUALITY_REPORT"},
        "agent_b": {"role": "architect", "task": "Review quality"},
        
        "need_to_know": {
            "read": [
                {"path": "TEST_RESULTS.md", "section": "Failure cases", "why": "analyze quality issues"},
                {"path": "BASELINE.md", "section": "Performance data", "why": "compare quality trends"}
            ],
            "skip": ["Phase 7-8 outputs", "full test logs"],
            "context": "single_phase"
        },
        
        "on_demand": {
            "trigger": "When root cause analysis is needed",
            "request_to": "Revert to Phase 3/4 to request detailed logs",
            "format": "Via GitHub Issue"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "After Phase 5 APPROVE",
                "tool": "sessions_spawn",
                "params": {"role": "qa"}
            },
            "quality_gate": {
                "when": "After QUALITY_REPORT is complete",
                "tool": "ConstitutionRunner",
                "threshold": "≥80%"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    },
    
    7: {
        "name": "Risk Management",
        "agent_a": {"role": "qa", "task": "Identify risks + develop mitigation"},
        "agent_b": {"role": "pm", "task": "Review risks"},
        
        "need_to_know": {
            "read": [
                {"path": "QUALITY_REPORT.md", "section": "Issues and Risks section", "why": "identify known risks"},
                {"path": "SRS.md", "section": "Constraints and Assumptions", "why": "identify potential risks"}
            ],
            "skip": ["Phase 8 outputs", "detailed code"],
            "context": "single_phase"
        },
        
        "on_demand": {
            "trigger": "When details of specific technical risks are needed",
            "request_to": "Revert to Phase 3 developer",
            "format": "Via GitHub commit comment"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "After Phase 6 APPROVE",
                "tool": "sessions_spawn",
                "params": {"role": "qa"}
            },
            "risk_matrix": {
                "when": "After risk identification is complete",
                "tool": "calculate_risk_score",
                "check": "Probability x Impact"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    },
    
    8: {
        "name": "Configuration Management",
        "agent_a": {"role": "devops", "task": "Establish configuration management system"},
        "agent_b": {"role": "pm", "task": "Review configuration"},
        
        "need_to_know": {
            "read": [
                {"path": "RISK_REGISTER.md", "section": "Known risks", "why": "configuration must support risk mitigation"},
                {"path": "BASELINE.md", "section": "Configuration snapshot", "why": "ensure reproducibility"},
                {"path": "QUALITY_REPORT.md", "section": "Issues", "why": "configuration must avoid known issues"}
            ],
            "skip": ["Other phase outputs"],
            "context": "single_phase"
        },
        
        "on_demand": {
            "trigger": "When version history of specific components is needed",
            "request_to": "Consult Git history",
            "format": "git log --oneline"
        },
        
        "tool_timing": {
            "spawn": {
                "when": "After Phase 7 APPROVE",
                "tool": "sessions_spawn",
                "params": {"role": "devops"}
            },
            "lock_deps": {
                "when": "After configuration is complete",
                "tool": "pip freeze > requirements.lock",
                "check": "Consistent with BASELINE.md"
            },
            "deployment_check": {
                "when": "After all configuration is complete",
                "tool": "verify_deployment_checklist",
                "check": "100% executable"
            }
        },
        
        "isolation": {
            "method": "SubagentIsolator.spawn",
            "fresh_messages": [],
            "log_format": '{"timestamp","role","task","session_id","commit"}'
        }
    }
}


def get_subagent_config(phase: int) -> dict:
    """Get sub-agent configuration for the specified Phase."""
    return PHASE_SUBAGENT.get(phase, PHASE_SUBAGENT[3])


def get_tool_timing(phase: int, event: str) -> dict:
    """Get the tools to use for a specific Phase at a specific event."""
    config = get_subagent_config(phase)
    return config.get("tool_timing", {}).get(event, {})


def get_need_to_know(phase: int) -> dict:
    """Get the Need-to-Know specification for the specified Phase."""
    config = get_subagent_config(phase)
    return config.get("need_to_know", {})


def get_on_demand_config(phase: int) -> dict:
    """Get the On-Demand specification for the specified Phase."""
    config = get_subagent_config(phase)
    return config.get("on_demand", {})


# Phase-specific Four-Dimensional Goals and Iteration Rounds
# This extends PHASE_SUBAGENT with iteration-specific information

PHASE_ITERATION = {
    1: {
        "name": "Requirements Specification",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "HR-15 citations", "method": "grep -c '\\[FR-' SRS.md"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "traceability": {"target": "10/10", "metric": "FR <-> NFR mapping", "method": "each FR has a corresponding NFR"}
        },
        "rounds": [
            {"round": 1, "goal": "Basic FR identification", "deliverable": "FR list + initial descriptions"},
            {"round": 2, "goal": "NFR supplement", "deliverable": "NFR list + constraints"},
            {"round": 3, "goal": "Interface specs", "deliverable": "API spec + error handling"},
            {"round": 4, "goal": "Traceability setup", "deliverable": "TRACEABILITY_MATRIX.md"},
            {"round": 5, "goal": "Full review", "deliverable": "SRS.md APPROVE"}
        ]
    },
    2: {
        "name": "Architecture Design",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "SAD <-> SRS consistency", "method": "each FR has a corresponding Module"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "modularity": {"target": "10/10", "metric": "Clear module boundaries", "method": "each Module has single responsibility"}
        },
        "rounds": [
            {"round": 1, "goal": "Base architecture", "deliverable": "SAD.md initial architecture"},
            {"round": 2, "goal": "Module boundaries", "deliverable": "Module interface definitions"},
            {"round": 3, "goal": "Interface definitions", "deliverable": "API spec + data flow"},
            {"round": 4, "goal": "ADR records", "deliverable": "ADR.md key decisions"},
            {"round": 5, "goal": "SAD <-> SRS verification", "deliverable": "SAD.md APPROVE"}
        ]
    },
    3: {
        "name": "Code Implementation",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "HR-15 citations + docstring [FR-XX]", "method": "grep -c '\\[FR-' 03-development/src/**/*.py"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "test_coverage": {"target": "10/10", "metric": "pytest PASS + coverage >= 80%", "method": "pytest --cov=03-development/src/ -v"}
        },
        "rounds": [
            {"round": 1, "goal": "Base implementation", "deliverable": "code + tests + pytest PASS"},
            {"round": 2, "goal": "Production-ready", "deliverable": "logging + error handling"},
            {"round": 3, "goal": "Stabilization", "deliverable": "pytest consistently PASS"},
            {"round": 4, "goal": "HR-15 implementation", "deliverable": "citations with line numbers"},
            {"round": 5, "goal": "A/B collaboration", "deliverable": "sessions_spawn.log complete"}
        ]
    },
    4: {
        "name": "Test Planning",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "FR <-> test mapping rate >= 90%", "method": "each FR has corresponding test cases"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "critical_path_coverage": {"target": "10/10", "metric": "Critical path coverage 100%", "method": "test coverage report"}
        },
        "rounds": [
            {"round": 1, "goal": "Test strategy", "deliverable": "TEST_PLAN.md test strategy"},
            {"round": 2, "goal": "Test cases", "deliverable": "test cases covering all FRs"},
            {"round": 3, "goal": "Environment setup", "deliverable": "isolated test environment"},
            {"round": 4, "goal": "Execute and record", "deliverable": "TEST_RESULTS.md"},
            {"round": 5, "goal": "Coverage analysis", "deliverable": "COVERAGE_REPORT.md"}
        ]
    },
    5: {
        "name": "Verification & Delivery",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "Baseline meets SRS constraints", "method": "performance data vs SRS"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "monitoring_coverage": {"target": "10/10", "metric": "Monitoring metric coverage 100%", "method": "MONITORING_PLAN.md completeness"}
        },
        "rounds": [
            {"round": 1, "goal": "Performance baseline", "deliverable": "BASELINE.md performance baseline"},
            {"round": 2, "goal": "Monitoring setup", "deliverable": "monitoring metric definitions"},
            {"round": 3, "goal": "Alert configuration", "deliverable": "reasonable alert thresholds"},
            {"round": 4, "goal": "Verification report", "deliverable": "VERIFICATION_REPORT.md"},
            {"round": 5, "goal": "Delivery preparation", "deliverable": "delivery checklist complete"}
        ]
    },
    6: {
        "name": "Quality Assurance",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "Constitution >= 80%", "method": "Constitution runner"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "logic_correctness": {"target": "10/10", "metric": "Logic correctness >= 90%", "method": "QUALITY_REPORT.md"}
        },
        "rounds": [
            {"round": 1, "goal": "Quality dimension definition", "deliverable": "quality dimension list"},
            {"round": 2, "goal": "Metric measurement", "deliverable": "metric data collection"},
            {"round": 3, "goal": "Issue analysis", "deliverable": "issue list + priority"},
            {"round": 4, "goal": "Fix plan", "deliverable": "fix plan document"},
            {"round": 5, "goal": "Final report", "deliverable": "QUALITY_REPORT.md APPROVE"}
        ]
    },
    7: {
        "name": "Risk Management",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "All risks have mitigation plans", "method": "RISK_REGISTER.md completeness"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "risk_assessment_validity": {"target": "10/10", "metric": "Probability x Impact is reasonable", "method": "risk score comparison"}
        },
        "rounds": [
            {"round": 1, "goal": "Risk identification", "deliverable": "risk list"},
            {"round": 2, "goal": "Assessment and classification", "deliverable": "risk scores + levels"},
            {"round": 3, "goal": "Mitigation plans", "deliverable": "RISK_MITIGATION_PLANS.md"},
            {"round": 4, "goal": "Responsibility assignment", "deliverable": "each risk has an owner"},
            {"round": 5, "goal": "Tracking mechanism", "deliverable": "tracking mechanism documented"}
        ]
    },
    8: {
        "name": "Configuration Management",
        "four_dimensional": {
            "spec_compliance": {"target": "10/10", "metric": "requirements.lock consistency", "method": "pip freeze == requirements.lock"},
            "ab_collaboration": {"target": "10/10", "metric": "sessions_spawn.log", "method": "1 record each for developer + reviewer"},
            "subagent_management": {"target": "10/10", "metric": "SubagentIsolator", "method": "fresh_messages isolation"},
            "deployment_check_coverage": {"target": "10/10", "metric": "Deployment checklist 100%", "method": "DEPLOYMENT_CHECKLIST.md completeness"}
        },
        "rounds": [
            {"round": 1, "goal": "Configuration audit", "deliverable": "CONFIG_RECORDS.md"},
            {"round": 2, "goal": "Dependency locking", "deliverable": "requirements.lock"},
            {"round": 3, "goal": "Environment specs", "deliverable": "ENVIRONMENT_SPEC.md"},
            {"round": 4, "goal": "Deployment scripts", "deliverable": "deployment scripts functional"},
            {"round": 5, "goal": "Final verification", "deliverable": "DEPLOYMENT_CHECKLIST.md APPROVE"}
        ]
    }
}


def get_phase_iteration(phase: int) -> dict:
    """Get the iteration configuration for the specified Phase."""
    return PHASE_ITERATION.get(phase, PHASE_ITERATION[3])


def get_four_dimensional_table(phase: int) -> str:
    """Generate a 4-dimension Markdown table."""
    iteration = get_phase_iteration(phase)
    lines = []
    lines.append("| Dimension | Target | Metric | Evaluation Method |")
    lines.append("|------|------|------|---------|")
    for dim, info in iteration["four_dimensional"].items():
        lines.append(f"| **{dim}** | {info['target']} | {info['metric']} | {info['method']} |")
    return "\n".join(lines)


def get_iteration_rounds_table(phase: int) -> str:
    """Generate an iteration rounds Markdown table."""
    iteration = get_phase_iteration(phase)
    lines = []
    lines.append("### Round Goals")
    lines.append("")
    lines.append("| Round | Goal | Deliverable |")
    lines.append("|-------|------|--------|")
    for r in iteration["rounds"]:
        lines.append(f"| Round {r['round']} | {r['goal']} | {r['deliverable']} |")
    return "\n".join(lines)

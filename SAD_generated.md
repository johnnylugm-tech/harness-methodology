# SAD - Harness Methodology

> On-demand Lazy Load template.
> Source: SKILL_TEMPLATES.md SS T2.1

## 1. Architecture Overview
The Harness Methodology project is a unified command-line interface (CLI) designed for advanced agent-based project management and execution. Its architecture is characterized by a modular, lazy-loaded design, where functionality is divided into loosely-coupled subsystems that are instantiated on-demand.

The core of the architecture is the `MethodologyCLI` class, which acts as a central orchestrator and entry point. It uses a factory pattern to manage and access its various subsystems, which cover areas like project planning, agent evaluation, security, and data management. A key feature is the "Steering Loop," which suggests a reactive, event-driven control flow. The system is also extensible, with an optional, more powerful "Ralph Mode" for advanced task persistence and scheduling.

## 2. Module Design

### 2.1 MethodologyCLI (Orchestrator)

| Attribute | Value |
|-----------|-------|
| Responsibility | Acts as the main entry point, dispatching commands to the appropriate subsystems. Manages the lifecycle of all other modules using a lazy-loading factory. |
| External Interface | Command-line arguments (e.g., `init`, `task add`, `sprint create`). |
| Dependencies | All other subsystems (dynamically loaded). |

#### Logical Constraints
- Must be the single entry point for all CLI commands.
- The `_FACTORIES` dictionary must be kept up-to-date with all available subsystems.

### 2.2 Security & Guardrails (`security_defense`, `anti_shortcut`)

| Attribute | Value |
|-----------|-------|
| Responsibility | Provides a multi-layered defense against unsafe operations. This includes validating inputs, sandboxing command execution, filtering outputs, and preventing "shortcut" behaviors like running dangerous commands. |
| External Interface | `InputValidator.validate()`, `ExecutionSandbox.run()`, `OutputFilter.clean()`, `CommandBlacklist.check()`, `ImpactAnalyzer.analyze()` |
| Dependencies | `os`, `subprocess`, `pathlib`. |

#### Logical Constraints
- All external commands must pass through the execution sandbox.
- All user-provided input that is used in commands must be validated.

### 2.3 Agent & Task Management (`ralph_mode`, `HITLController`, `AgentEvaluator`)

| Attribute | Value |
|-----------|-------|
| Responsibility | Manages the lifecycle of tasks and the evaluation of AI agents. This includes persisting task state, scheduling execution (`RalphScheduler`), tracking progress, and facilitating human-in-the-loop (HITL) for approvals and quality checks. |
| External Interface | `TaskPersistence.save()`, `AgentEvaluator.run_suite()`, `HITLController.request_approval()` |
| Dependencies | `SessionManager`, `sqlite3` (likely for persistence), `MessageBus`. |

#### Logical Constraints
- All agent actions that have external impact should require an approval flow.
- Task state must be persisted to handle interruptions and enable long-running operations.

## 3. Error Handling
| Level | Handling Strategy |
|-------|------------------|
| L1 (Validation) | Immediate return with an error message to the user (e.g., invalid command). |
| L2 (Execution) | Commands are sandboxed. Execution errors are caught, logged, and reported. Risky operations may require double confirmation. |
| L3 (System) | Graceful degradation. For example, if "Ralph Mode" is not available, the CLI continues to function with a core set of features. |

## 4. Technology Choices
| Technology | Rationale |
|------------|----------|
| Python 3 | The de-facto language for AI/ML and CLI tool development, with a rich ecosystem of libraries. |
| argparse | Standard Python library for parsing command-line arguments, providing a robust and familiar interface. |
| Lazy-Loading Factory | Architectural pattern chosen to ensure the CLI has a fast startup time and efficient memory usage by only loading modules as they are needed. |
| JSON / Files | Used for configuration, state persistence, and data exchange between modules, offering simplicity and human-readability. |

---

## 5. SAB Block (machine-readable)

<!-- SAB:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-04-27",
  "phase": 2,
  "project": "harness-methodology",
  "layers": [
    {
      "name": "0_CLI_Entry",
      "modules": ["cli.py"],
      "allowed_dependencies": ["1_Orchestration", "2_Subsystems"]
    },
    {
      "name": "1_Orchestration",
      "modules": ["steering", "harness", "ralph_mode"],
      "allowed_dependencies": ["2_Subsystems", "3_Core_Logic"]
    },
    {
      "name": "2_Subsystems",
      "modules": ["security_defense", "anti_shortcut", "agent_evaluator", "sprint_planner", "progress_dashboard", "kill_switch"],
      "allowed_dependencies": ["3_Core_Logic"]
    },
    {
      "name": "3_Core_Logic",
      "modules": ["core", "schemas", "gap_detector"],
      "allowed_dependencies": []
    }
  ],
  "dependencies": {
    "0_CLI_Entry": ["1_Orchestration", "2_Subsystems"],
    "1_Orchestration": ["2_Subsystems", "3_Core_Logic"],
    "2_Subsystems": ["3_Core_Logic"]
  },
  "quality_targets": {
    "max_complexity": 25,
    "min_coverage": 75,
    "max_coupling": 0.4
  }
}
```
<!-- SAB:END -->

Note: Fill in the JSON above — it is used for Drift Detection.

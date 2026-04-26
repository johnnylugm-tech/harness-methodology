#!/usr/bin/env python3
"""
Methodology CLI - Unified Command Line Interface

Usage:
    python cli.py init "project-name"
    python cli.py task add "task" --points 5
    python cli.py sprint create
    python cli.py board
    python cli.py report
    python cli.py status
"""

import sys
import os
import argparse
import json
import subprocess
from datetime import datetime
import re
from pathlib import Path
import cli_phase_prompts
import cli_phase_subagent

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from progress_dashboard import ProgressDashboard
from gantt_chart import GanttChart, ResourceGanttChart
from message_bus import MessageBus
from orchestration import run_constitution_check_with_feedback
from sprint_planner import SprintPlanner
from pm_terminology import PMTerminologyMapper
from resource_dashboard import ResourceDashboard
from pm_mode import PMMode
from agent_evaluator import AgentEvaluator, HumanEvaluator, TestCase, EvaluationSuite
from structured_output import StructuredOutputEngine
from data_quality import DataQualityChecker
from smart_router import route_by_phase
from enterprise_hub import EnterpriseHub
from langgraph_migrator import LangGraphMigrationTool
from crewai_bridge import FrameworkBridge, bridge_quick_convert
from wizard.wizard import SetupWizard, TEMPLATES
from onboarding.wizard import run_phase, load_progress
from guardrails.guardrails import Guard
from autoscaler.autoscaler import AutoScaler
from data_connector import DataSourceManager
from agent_debugger import AgentDebugger, EventType
from approval_flow import ApprovalFlow, ApprovalLevel, ApprovalStatus
from risk_registry import RiskRegistry, RiskLevel, RiskStatus
from p2p_team_config import P2PTeamConfig
from hitl_controller import HITLController, AgentOwner, OutputStatus
from anti_shortcut.blacklist import CommandBlacklist, ViolationSeverity
from anti_shortcut.audit_logger import AIAuditLogger, ActionType
from anti_shortcut.double_confirm import DoubleConfirmation, ConfirmationLevel
from anti_shortcut.impact_analysis import ImpactAnalyzer
from security_defense import (
    InputValidator,
    ExecutionSandbox,
    OutputFilter,
    HumanInTheLoop,
    SandboxConfig,
    SandboxLevel,
    ApprovalLevel,
)
from code_metrics import MetricsTracker
from checkpoint_manager import SessionManager

# Ralph Mode (optional - CLI still works if ralph_mode is unavailable)
try:
    from ralph_mode.cli import (
        RalphCLI,
        TaskState,
    )
    from ralph_mode.task_persistence import TaskPersistence
    from ralph_mode.scheduler import (
        RalphScheduler,
        SchedulerConfig,
        SchedulerManager,
    )
    from ralph_mode.progress_tracker import RalphProgressTracker
    from ralph_mode.state_machine import PhaseStateMachine
    RALPH_MODE_AVAILABLE = True
except ImportError:
    RALPH_MODE_AVAILABLE = False

from tool_registry import ToolRegistry, TOOL_HANDLERS

# Steering Loop
from steering.steering_loop import SteeringLoop, SteeringConfig


class MethodologyCLI:
    """Unified CLI entry point"""
    
    VERSION = "6.102.0"

    # Lazy-loading subsystem factories
    _FACTORIES = {
        "progress": ProgressDashboard,
        "gantt": GanttChart,
        "bus": MessageBus,
        "sprint_planner": SprintPlanner,
        "terminology": PMTerminologyMapper,
        "pm_mode": PMMode,
        "evaluator": AgentEvaluator,
        "hitl": HumanEvaluator,
        "structured": StructuredOutputEngine,
        "data_quality": DataQualityChecker,
        "enterprise": EnterpriseHub,
        "migrator": LangGraphMigrationTool,
        "bridge": FrameworkBridge,
        "wizard": SetupWizard,
        "guardrails": Guard,
        "autoscaler": AutoScaler,
        "resources": ResourceDashboard,
        "data_manager": DataSourceManager,
        "debugger": AgentDebugger,
        "approval_flow": ApprovalFlow,
        "registry": RiskRegistry,
        "hitl_controller": HITLController,
        "blacklist": CommandBlacklist,
        "ai_audit": AIAuditLogger,
        "input_validator": InputValidator,
        "execution_sandbox": lambda: ExecutionSandbox(SandboxConfig(level=SandboxLevel.STRICT)),
        "output_filter": OutputFilter,
        "hitl_security": HumanInTheLoop,
        "ralph_cli": RalphCLI,
        "ralph_persistence": TaskPersistence,
        "ralph_scheduler_manager": SchedulerManager,
        "session_manager": SessionManager,
    }

    def __init__(self):
        self._cache: dict = {}
        self.p2p_config: P2PTeamConfig = None

    def __getattr__(self, name: str):
        if name in self._FACTORIES:
            obj = self._FACTORIES[name]()
            self._cache[name] = obj
            return obj
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
    
    def _check_command(self, command: str) -> bool:
        """Check if command is dangerous"""
        blocked = self.blacklist.check(command)
        if blocked:
            pass # Removed print-debug
            return False
        return True
    
    def run(self, args):
        """Execute command"""
        command = args.command
        
        if command == "init":
            return self.cmd_init(args)
        elif command == "finish":
            return self.cmd_finish(args)
        elif command == "task":
            return self.cmd_task(args)
        elif command == "sprint":
            return self.cmd_sprint(args)
        elif command == "board":
            return self.cmd_board(args)
        elif command == "gantt-resource":
            return self.cmd_gantt_resource(args)
        elif command == "report":
            return self.cmd_report(args)
        elif command == "status":
            return self.cmd_status(args)
        elif command == "phase-status":
            return self.cmd_phase_status(args)
        elif command == "phase-pause":
            return self.cmd_phase_pause(args)
        elif command == "phase-resume":
            return self.cmd_phase_resume(args)
        elif command == "phase-freeze":
            return self.cmd_phase_freeze(args)
        elif command == "ab-history":
            return self.cmd_ab_history(args)
        elif command == "ab-record":
            return self.cmd_ab_record(args)
        elif command == "audit-heatmap":
            return self.cmd_audit_heatmap(args)
        elif command == "time-check":
            return self.cmd_time_check(args)
        elif command == "resources":
            return self.cmd_resources(args)
        elif command == "bus":
            return self.cmd_bus(args)
        elif command == "constitution":
            return self.cmd_constitution(args)
        elif command == "constitution-sync":
            return self.cmd_constitution_sync(args)
        elif command == "run-phase":
            return self.cmd_run_phase(args)
        elif command == "auto-research":
            return self.cmd_auto_research(args)
        elif command == "plan-phase":
            return self.cmd_plan_phase(args)
        elif command == "spec-track":
            return self.cmd_spec_track(args)
        elif command == "version":
            return self.cmd_version(args)
        elif command == "term":
            return self.cmd_term(args)
        elif command == "pm":
            return self.cmd_pm(args)
        elif command == "eval":
            return self.cmd_eval(args)
        elif command == "quality":
            return self.cmd_quality(args)
        elif command == "enterprise":
            return self.cmd_enterprise(args)
        elif command == "audit":
            return self.cmd_audit(args)
        elif command == "migrate":
            return self.cmd_migrate(args)
        elif command == "wizard":
            return self.cmd_wizard(args)
        elif command == "guardrails":
            return self.cmd_guardrails(args)
        elif command == "security":
            return self.cmd_security(args)
        elif command == "scale":
            return self.cmd_scale(args)
        elif command == "parse":
            return self.cmd_parse(args)
        elif command == "resources":
            return self.cmd_resources(args)
        elif command == "debug":
            return self.cmd_debug(args)
        elif command == "trace":
            return self.cmd_trace(args)
        elif command == "trace-check":
            return self.cmd_trace_check(args)
        elif command == "approval":
            return self.cmd_approval(args)
        elif command == "risk":
            return self.cmd_risk(args)
        elif command == "p2p":
            return self.cmd_p2p(args)
        elif command == "hitl":
            return self.cmd_hitl(args)
        elif command == "integrity":
            return self.cmd_integrity(args)
        elif command == "gatekeeper":
            return self.cmd_gatekeeper(args)
        elif command == "confirmations":
            return self.cmd_confirmations(args)
        elif command == "release":
            return self.cmd_release(args)
        elif command == "policy":
            return self.cmd_policy(args)
        elif command == "install-hook":
            return self.cmd_install_hook(args)
        elif command == "enforcement-config":
            return self.cmd_enforcement_config(args)
        elif command == "enforcement":
            return self.cmd_enforcement(args)
        elif command == "quality-gate" or command == "qg":
            return self.cmd_quality_gate(args)
        elif command == "enforce":
            return self.cmd_enforce(args)
        elif command == "decision" or command == "dec":
            return self.cmd_decision_gate(args)
        elif command == "agent-proof-hook":
            return self.cmd_agent_proof_hook(args)
        elif command == "memory":
            return self.cmd_memory(args)
        elif command == "roi":
            return self.cmd_roi(args)
        elif command == "m27":
            return self.cmd_m27(args)
        elif command == "metrics":
            return self.cmd_metrics(args)
        elif command == "debt":
            return self.cmd_debt(args)
        elif command == "adr":
            return self.cmd_adr(args)
        elif command == "guide":
            return self.cmd_guide(args)
        elif command == "roadmap":
            return self.cmd_roadmap(args)
        elif command == "persona":
            return self.cmd_persona(args)
        elif command == "ralph":
            return self.cmd_ralph(args)
        elif command == "stage-pass":
            return self.cmd_stage_pass(args)
        elif command == "phase-verify":
            return self.cmd_phase_truth(args)
        elif command == "skill-check":
            return self.cmd_skill_check(args)
        elif command == "model-recommend":
            return self.cmd_model_recommend(args)
        elif command == "context-compress":
            return self.cmd_context_compress(args)
        elif command == "update-project-status":
            return self.cmd_update_project_status(args)
        elif command == "update-step":
            return self.cmd_update_step(args)
        elif command == "end-phase":
            return self.cmd_end_phase(args)
        elif command == "update-artifact":
            return self.cmd_update_artifact(args)
        elif command == "add-task":
            return self.cmd_add_task(args)
        elif command == "task-result":
            return self.cmd_task_result(args)
        elif command == "verify-artifact":
            return self.cmd_verify_artifact(args)
        elif command == "retry-test":
            return self.cmd_retry_test(args)
        elif command == "fsm-status":
            return self.cmd_fsm_status(args)
        elif command == "fsm-transition":
            return self.cmd_fsm_transition(args)
        elif command == "fsm-resume":
            return self.cmd_fsm_resume(args)
        elif command == "fsm-unfreeze":
            return self.cmd_fsm_unfreeze(args)
        elif command == "session-save":
            return self.cmd_session_save(args)
        elif command == "session-load":
            return self.cmd_session_load(args)
        elif command == "session-list":
            return self.cmd_session_list(args)
        elif command == "session-delete":
            return self.cmd_session_delete(args)
        elif command == "tool-registry":
            return self.cmd_tool_registry(args)
        elif command == "plan-phase":
            return self.cmd_plan_phase(args)
        elif command == "onboarding":
            return self.cmd_onboarding(args)
        elif command == "steering":
            return self.cmd_steering(args)
        else:
            pass # Removed print-debug
            return 1
        
        return 0
    
    # ==================== Commands ====================
    
    def cmd_init(self, args):
        """Initialize project"""
        project_name = args.name or "my-project"
        
        pass # Removed print-debug
        
        # Create project structure
        dirs = [".methodology", ".methodology/tasks", ".methodology/sprints"]
        for d in dirs:
            os.makedirs(d, exist_ok=True)
        
        # Save initial state
        self.progress.save()
        
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        
        # Start quality_watch daemon
        import subprocess
        project_path = Path.cwd()
        subprocess.Popen(
            [sys.executable, "quality_watch.py", "start", "--project", str(project_path)],
            cwd=str(project_path)
        )
        
        # ========================================
        # Ralph Mode auto-start (added in v5.83)
        # ========================================
        try:
            from ralph_mode import RalphScheduler, TaskPersistence
            from ralph_mode.task_persistence import TaskState
            
            # Initialize task state
            persistence = TaskPersistence()
            state = TaskState(
                task_id=project_name,
                status="running",
                current_phase="init"
            )
            persistence.save_state(state)
            
            # Start RalphScheduler background monitoring (5-minute polling)
            scheduler = RalphScheduler(
                task_id=project_name,
                interval_seconds=300,  # 5 minutes
                daemon=True
            )
            scheduler.start()
            
            pass # Removed print-debug
        except ImportError as e:
            pass # Removed print-debug
        
        pass # Removed print-debug
        pass # Removed print-debug
        
        return 0
    
    def cmd_finish(self, args):
        """Finish project and stop quality watch"""
        project_path = Path.cwd()
        
        # Stop quality_watch
        result = subprocess.run(
            [sys.executable, "quality_watch.py", "stop", "--project", str(project_path)],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print(f"❌ Failed to stop quality_watch: {result.stderr.strip()}")
            return result.returncode
        
        print("✅ Project finished, quality_watch stopped")
        return 0
    
    def cmd_task(self, args):
        """Task management"""
        action = args.action
        
        if action == "add":
            item_id = self.progress.add_to_backlog(
                args.title or "New Task",
                story_points=args.points or 1,
                priority=args.priority or 3
            )
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            
        elif action == "list":
            self.progress.load()
            items = list(self.progress.backlog.values())
            if not items:
                pass # Removed print-debug
                return 0
            
            pass # Removed print-debug
            pass # Removed print-debug
            for item in items:
                pass # Removed print-debug
            
        elif action == "complete":
            if self.progress.mark_item_completed(args.task_id):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
        
        return 0
    
    def cmd_sprint(self, args):
        """Sprint management"""
        action = args.action
        
        if action == "create":
            sprint = self.sprint_planner.create_sprint(
                name=args.name or "Sprint 1",
                start_date=args.start or datetime.now().strftime("%Y-%m-%d"),
                end_date=args.end or (datetime.now().replace(day=14)).strftime("%Y-%m-%d"),
                capacity=args.capacity or 40
            )
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            
        elif action == "list":
            sprints = list(self.sprint_planner.sprints.values())
            if not sprints:
                pass # Removed print-debug
                return 0
            
            pass # Removed print-debug
            pass # Removed print-debug
            for s in sprints:
                status = "active" if s.is_active else ("completed" if s.is_completed else "planning")
                pass # Removed print-debug
        
        elif action == "start":
            if self.sprint_planner.start_sprint(args.sprint_id):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
        
        return 0
    
    def cmd_board(self, args):
        """Open visual dashboard"""
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        
        # Progress
        summary = {"total_items": len(self.progress.backlog), "completed_items": sum(1 for i in self.progress.backlog.values() if i.completed)}
        
        pass # Removed print-debug
        if summary:
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
        else:
            pass # Removed print-debug
        
        pass # Removed print-debug
        
        # Gantt
        if self.gantt.tasks:
            pass # Removed print-debug
            pass # Removed print-debug
        else:
            pass # Removed print-debug
        
        pass # Removed print-debug
        
        # Message Bus
        pass # Removed print-debug
        pass # Removed print-debug
        
        return 0
    
    def cmd_report(self, args):
        """Generate report"""
        report_type = args.type or "weekly"
        
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        
        # Progress
        pass # Removed print-debug
        summary = {"total_items": len(self.progress.backlog), "completed_items": sum(1 for i in self.progress.backlog.values() if i.completed)}
        if summary:
            for key, value in summary.items():
                pass # Removed print-debug
        pass # Removed print-debug
        
        # Data sources
        pass # Removed print-debug
        connections = self.data_manager.list_connections()
        if connections:
            for conn in connections:
                status = "✅" if conn['connected'] else "❌"
                pass # Removed print-debug
        else:
            pass # Removed print-debug
        pass # Removed print-debug
        
        # Save report
        if args.output:
            with open(args.output, 'w') as f:
                f.write(f"# {report_type.upper()} Report\n\n")
                f.write(f"Generated: {datetime.now().isoformat()}\n\n")
                f.write(json.dumps(summary, indent=2, default=str))
            pass # Removed print-debug
        
        return 0
    
    def cmd_status(self, args):
        """Show status"""
        self.progress.load()
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        
        # Tasks
        items = list(self.progress.backlog.values())
        pass # Removed print-debug
        
        # Sprints
        sprints = list(self.sprint_planner.sprints.values())
        active = sum(1 for s in sprints if s.is_active)
        pass # Removed print-debug
        
        # Message Bus
        bus_status = self.bus.get_queue_status()
        pass # Removed print-debug
        
        # Data Sources
        connections = self.data_manager.list_connections()
        connected = sum(1 for c in connections if c['connected'])
        pass # Removed print-debug
        
        return 0
    
    def cmd_phase_status(self, args):
        """Show Phase execution status"""
        from pathlib import Path
        phase = args.phase
        state_path = Path(".methodology/state.json")
        
        if not state_path.exists():
            print(f"❌ state.json not found. Phase {phase} may not have started yet.")
            return 1
        
        state = json.loads(state_path.read_text())
        ps = state.get("phase_state", {})
        
        # Calculate elapsed time
        started = ps.get("started_at", None)
        elapsed = "N/A"
        if started:
            from datetime import datetime, timezone
            start_time = datetime.fromisoformat(started)
            elapsed_min = (datetime.now(timezone.utc) - start_time).seconds // 60
            elapsed = f"{elapsed_min} min"
        
        # Status display (RUNNING / PAUSE / FREEZE / COMPLETED)
        phase_status = ps.get("status", "RUNNING")
        status_icon = {"RUNNING": "🟢", "PAUSE": "⏸️", "FREEZE": "🔒", "COMPLETED": "✅"}.get(phase_status, "⚪")
        
        # Integrity score
        integrity = ps.get("integrity_score", 100)
        integrity_icon = "🔒" if integrity < 40 else ("⚠️" if integrity < 60 else "✅")
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Phase {phase} Runtime Status                                  ║
╠══════════════════════════════════════════════════════════════╣
║  Current Phase:     {state.get('current_phase', 'N/A'):<35}║
║  Status:           {status_icon} {phase_status:<32}║
║  Started At:        {started or 'N/A':<35}║
║  Elapsed:           {elapsed:<35}║
╠══════════════════════════════════════════════════════════════╣
║  Metrics                                                        ║
║  ├── BLOCK Count:      {ps.get('blocks', 0):<30}║
║  ├── A/B Rounds:      {ps.get('ab_rounds', 0):<30}║
║  ├── Integrity Score: {integrity_icon} {integrity:<29}║
║  ├── Warnings:        {ps.get('warnings', 0):<30}║
║  └── Last Gate Score: {ps.get('last_gate_score', 'N/A'):<30}║
╠══════════════════════════════════════════════════════════════╣
║  Alerts ({len(state.get('trend_alerts', []))})                                                  ║
""")
        
        alerts = state.get("trend_alerts", [])
        if alerts:
            for alert in alerts:
                print(f"  🚨 {alert.get('type')}: {alert.get('current')} (threshold: {alert.get('threshold')})")
        else:
            print("  ✅ No active alerts")
        
        print("╚══════════════════════════════════════════════════════════════╝")
        return 0
    
    def cmd_phase_pause(self, args):
        """Pause Phase execution"""
        from pathlib import Path
        phase = args.phase
        state_path = Path(".methodology/state.json")
        
        if not state_path.exists():
            print(f"❌ state.json not found.")
            return 1
        
        state = json.loads(state_path.read_text())
        ps = state.get("phase_state", {})
        
        # If already FREEZE, reject PAUSE
        if ps.get("status") == "FREEZE":
            print("❌ Cannot PAUSE a FREEZEd project. Use 'phase-resume' after unfreezing.")
            return 1
        
        ps["status"] = "PAUSE"
        state["phase_state"] = ps
        state_path.write_text(json.dumps(state, indent=2))
        
        print(f"⏸️  Phase {phase} PAUSED")
        print("    Use 'phase-resume' to continue execution.")
        return 0
    
    def cmd_phase_resume(self, args):
        """Resume Phase execution"""
        from pathlib import Path
        phase = args.phase
        state_path = Path(".methodology/state.json")
        
        if not state_path.exists():
            print(f"❌ state.json not found.")
            return 1
        
        state = json.loads(state_path.read_text())
        ps = state.get("phase_state", {})
        
        # If FREEZE, must UNFREEZE first
        if ps.get("status") == "FREEZE":
            print("❌ Cannot RESUME a FREEZEd project. Use 'phase-unfreeze' first.")
            return 1
        
        ps["status"] = "RUNNING"
        state["phase_state"] = ps
        state_path.write_text(json.dumps(state, indent=2))
        
        print(f"▶️  Phase {phase} RESUMED")
        return 0
    
    def cmd_integrity(self, args):
        """HR-14 Integrity score calculation - per SKILL.md §6 formula"""
        from pathlib import Path
        import json
        import os

        project_path = Path(args.project)
        state_file = project_path / ".methodology" / "state.json"
        spawn_log = project_path / "sessions_spawn.log"

        # Read state.json
        if not state_file.exists():
            print("❌ state.json not found, verify --project path")
            return 1

        with open(state_file) as f:
            state = json.load(f)

        current_phase = state.get("current_phase", 0)

        # Phase Completeness: estimated from artifact existence
        phase_deliverables = {
            1: ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md"],
            2: ["SAD.md", "ADR.md"],
            3: ["app"],
            4: ["TEST_PLAN.md", "TEST_RESULTS.md"],
            5: ["BASELINE.md", "MONITORING_PLAN.md"],
            6: ["QUALITY_REPORT.md"],
            7: ["RISK_REGISTER.md"],
            8: ["CONFIG_RECORDS.md"],
        }

        # Constitution score: from state or estimated
        constitution_scores = state.get("constitution_scores", {})
        for p in range(1, 9):
            if p not in constitution_scores:
                constitution_scores[p] = 0

        # Phase Completeness
        phase_completions = {}
        for phase, deliverables in phase_deliverables.items():
            if phase > current_phase:
                phase_completions[phase] = 0.0
            elif phase == current_phase:
                # Current phase counts as 50%
                phase_completions[phase] = 0.5
            else:
                # Completed phase, check artifacts
                present = sum(1 for d in deliverables if (project_path / d).exists()) / len(deliverables)
                phase_completions[phase] = present

        # Log Completeness: sessions_spawn.log
        if spawn_log.exists():
            with open(spawn_log) as f:
                lines = f.readlines()
            total_expected = sum(len(d) for d in phase_deliverables.values())
            log_completeness = min(len(lines) / max(total_expected, 1), 1.0)
        else:
            log_completeness = 0.0

        # SKILL.md §6 Integrity formula
        weights = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.15,
                    5: 0.10, 6: 0.10, 7: 0.10, 8: 0.10}

        integrity = 0.0
        for phase, completion in phase_completions.items():
            constitution = constitution_scores.get(phase, 0)
            phase_score = completion * (constitution / 100.0) * log_completeness
            integrity += phase_score * weights.get(phase, 0)

        integrity_pct = integrity * 100

        # HR-14 FREEZE determination
        if integrity_pct < 40:
            hr14_icon = "🔒 FREEZE"
            hr14_msg = "HR-14 triggered! Full audit required"
        elif integrity_pct < 60:
            hr14_icon = "⚠️ WARNING"
            hr14_msg = "Integrity low, review lagging Phase"
        else:
            hr14_icon = "✅ HEALTHY"
            hr14_msg = "Integrity nominal"

        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  HR-14 Integrity Score                                           ║
╠══════════════════════════════════════════════════════════════════╣
║  Integrity Score: {hr14_icon:<50}║
║  {integrity_pct:>6.1f}%{' ':>44}║
╠══════════════════════════════════════════════════════════════════╣
║  Component Factors                                               ║""")

        for phase in range(1, 9):
            completion = phase_completions.get(phase, 0)
            constitution = constitution_scores.get(phase, 0)
            weight = weights.get(phase, 0)
            contrib = completion * (constitution / 100.0) * log_completeness * weight * 100
            status = "✅" if phase <= current_phase else "  "
            if phase == current_phase:
                status = "▶️"
            print(f"║  {status} Phase {phase}: completions={completion:.0%}  "
                  f"constitution={constitution:>4.0f}%  weight={weight:.0%}  "
                  f"contrib={contrib:>5.1f}%     ║")

        print(f"""╠══════════════════════════════════════════════════════════════════╣
║  Log Completeness: {log_completeness:.0%} (sessions_spawn.log)                        ║
║  Current Phase:   {current_phase}                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  {hr14_msg:<62}  ║
╚══════════════════════════════════════════════════════════════════╝
""")
        return 0

    def cmd_phase_freeze(self, args):
        """Freeze project (used when HR-14 triggered)"""
        from pathlib import Path
        state_path = Path(".methodology/state.json")
        
        if not state_path.exists():
            print(f"❌ state.json not found.")
            return 1
        
        state = json.loads(state_path.read_text())
        ps = state.get("phase_state", {})
        
        ps["status"] = "FREEZE"
        state["phase_state"] = ps
        state_path.write_text(json.dumps(state, indent=2))
        
        print(f"🔒 Project FREEZED")
        print("    HR-14 triggered: Integrity < 40")
        print("    Full audit required before resuming.")
        print("    After audit, use 'phase-unfreeze' to unlock.")
        return 0
    
    def cmd_ab_history(self, args):
        """Show A/B round-trip history"""
        from pathlib import Path
        phase = args.phase
        state_path = Path(".methodology/state.json")
        
        if not state_path.exists():
            print(f"❌ state.json not found.")
            return 1
        
        state = json.loads(state_path.read_text())
        history = state.get("history", [])
        
        # Filter BLOCK and AB_ROUND events for specified Phase
        phase_events = [
            e for e in history
            if e.get("phase") == phase and e.get("event") in ["BLOCK", "AB_ROUND", "PHASE_START"]
        ]
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Phase {phase} A/B History                                       ║
╠══════════════════════════════════════════════════════════════╣""")
        
        if not phase_events:
            print("║  No A/B events recorded for this phase.")
        else:
            for i, event in enumerate(phase_events, 1):
                event_type = event.get("event", "UNKNOWN")
                timestamp = event.get("timestamp", "N/A")[:19]
                if event_type == "BLOCK":
                    icon = "🔴"
                    detail = f"violations: {event.get('violations', 0)}"
                elif event_type == "AB_ROUND":
                    icon = "↔️"
                    detail = "A/B exchange"
                elif event_type == "PHASE_START":
                    icon = "🚀"
                    detail = "Phase started"
                else:
                    icon = "⚪"
                    detail = ""
                
                print(f"║  {i:2}. {icon} [{timestamp}] {event_type:<12} {detail:<20}")
        
        print("╚══════════════════════════════════════════════════════════════╝")
        return 0
    
    def cmd_ab_record(self, args):
        """Record one A/B round-trip"""
        from quality_gate import UnifiedGate
        phase = args.phase
        notes = args.notes or ""
        
        gate = UnifiedGate(args.project if hasattr(args, 'project') else ".")
        gate._update_state(event="AB_ROUND", phase=phase, notes=notes)
        print(f"✅ Recorded AB_ROUND for Phase {phase}")
        if notes:
            print(f"   Notes: {notes}")
        return 0
    
    def cmd_audit_heatmap(self, args):
        """Show cross-project failure heatmap"""
        # TODO: requires cross-project data, currently just a scaffold
        print("""
╔══════════════════════════════════════════════════════════════╗
║  Cross-Project Failure Heatmap                                   ║
╠══════════════════════════════════════════════════════════════╣
║  Note: set project path to display heatmap                        ║
║                                                                        ║
║  Usage:                                                             ║
║    python cli.py audit-heatmap --projects /path/to/project1,/path2 ║
╚══════════════════════════════════════════════════════════════════╝
""")
        return 0
    
    def cmd_time_check(self, args):
        """Check Phase duration"""
        from pathlib import Path
        phase = args.phase
        threshold = args.threshold
        state_path = Path(".methodology/state.json")
        
        if not state_path.exists():
            print(f"❌ state.json not found.")
            return 1
        
        state = json.loads(state_path.read_text())
        ps = state.get("phase_state", {})
        started = ps.get("started_at")
        
        if not started:
            print(f"❌ Phase {phase} start time not recorded.")
            return 1
        
        from datetime import datetime, timezone
        start_time = datetime.fromisoformat(started)
        elapsed_min = (datetime.now(timezone.utc) - start_time).seconds // 60
        
        status = "✅" if elapsed_min <= threshold else "🚨"
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Phase {phase} Time Check                                         ║
╠══════════════════════════════════════════════════════════════╣
║  Threshold: {threshold} minutes                                         ║
║  Elapsed:   {elapsed_min} minutes                                         ║
║                                                                        ║
║  {status} {"OK" if elapsed_min <= threshold else "EXCEEDED!"}                                                            ║
╚══════════════════════════════════════════════════════════════════╝
""")
        
        return 0 if elapsed_min <= threshold else 1
    
    def cmd_resources(self, args):
        """Resource management"""
        action = args.action
        
        if action == "list":
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
        
        return 0
    
    def cmd_bus(self, args):
        """Message Bus management"""
        action = args.action
        
        if action == "status":
            pass # Removed print-debug
        
        elif action == "tree":
            pass # Removed print-debug
        
        elif action == "watch":
            seconds = args.seconds or 10
            pass # Removed print-debug
            self.bus.watch(seconds)
        
        return 0
    
    def cmd_eval(self, args):
        """Agent Evaluation"""
        action = args.eval_action
        
        if action == "create":
            suite = self.evaluator.create_suite(
                name=args.name or "Test Suite",
                description=args.description or "",
                iterations=args.iterations or 1
            )
            pass # Removed print-debug
            pass # Removed print-debug
            return 0
        
        elif action == "list":
            suites = list(self.evaluator.suites.values())
            if not suites:
                pass # Removed print-debug
                return 0
            
            pass # Removed print-debug
            pass # Removed print-debug
            for s in suites:
                pass # Removed print-debug
            return 0
        
        elif action == "report":
            if not args.suite_id:
                pass # Removed print-debug
                return 1
            
            if args.suite_id not in self.evaluator.suites:
                pass # Removed print-debug
                return 1
            
            pass # Removed print-debug
            return 0
        
        elif action == "run":
            if not args.suite_id:
                pass # Removed print-debug
                return 1
            
            if args.suite_id not in self.evaluator.suites:
                pass # Removed print-debug
                return 1
            
            # Define simple mock agent
            import time
            def mock_agent(prompt, context=None, timeout=30):
                time.sleep(0.1)
                return f"Response to: {prompt[:50]}..."
            
            pass # Removed print-debug
            self.evaluator.run_suite(args.suite_id, mock_agent)
            pass # Removed print-debug
            return 0
        
        elif action == "add":
            if not args.suite_id:
                pass # Removed print-debug
                return 1
            
            if not args.name:
                pass # Removed print-debug
                return 1
            
            test = self.evaluator.add_test_case(
                suite_id=args.suite_id,
                name=args.name,
                input_prompt=args.prompt or "Test prompt",
                expected_output=args.expected
            )
            pass # Removed print-debug
            return 0
        
        elif action == "hitl":
            pending = self.hitl.get_pending_count()
            pass # Removed print-debug
            pass # Removed print-debug
            return 0
        
        pass # Removed print-debug
        return 1
    
    def cmd_quality(self, args):
        """Data Quality Checker"""
        action = args.action
        
        if action == "check":
            pass # Removed print-debug
            pass # Removed print-debug
            # Generate sample data
            sample_data = [
                {"id": 1, "name": "Alice", "email": "alice@test.com", "age": 30},
                {"id": 2, "name": "Bob", "email": "bob@test.com", "age": 25},
                {"id": 3, "name": "", "email": "invalid", "age": None},
            ]
            report = self.data_quality.analyze(sample_data)
            pass # Removed print-debug
        elif action == "report":
            pass # Removed print-debug
        return 0
    
    def cmd_enterprise(self, args):
        """Enterprise Integration Hub"""
        action = args.action
        
        if action == "status":
            pass # Removed print-debug
            pass # Removed print-debug
            status = self.enterprise.get_status()
            for k, v in status.items():
                pass # Removed print-debug
        elif action == "audit":
            pass # Removed print-debug
        return 0
    
    def cmd_audit(self, args):
        """AI Audit Logger - AI operation audit"""
        action = args.action
        
        if action == "status":
            pass # Removed print-debug
            pass # Removed print-debug
            report = self.ai_audit.get_audit_report()
            pass # Removed print-debug
            pass # Removed print-debug
            for op_type, count in report['by_type'].items():
                pass # Removed print-debug
            pass # Removed print-debug
        
        elif action == "anomalies":
            pass # Removed print-debug
            pass # Removed print-debug
            agent_id = getattr(args, 'agent', None)
            severity = getattr(args, 'severity', None)
            
            anomalies = self.ai_audit.get_anomalies(agent_id=agent_id, severity=severity)
            
            if not anomalies:
                pass # Removed print-debug
                return 0
            
            for i, anomaly in enumerate(anomalies, 1):
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
        
        elif action == "report":
            pass # Removed print-debug
            pass # Removed print-debug
            agent_id = getattr(args, 'agent', None)
            report = self.ai_audit.get_audit_report(agent_id=agent_id)
            pass # Removed print-debug
        
        return 0
    
    def cmd_wizard(self, args):
        """Setup Wizard"""
        pass # Removed print-debug
        pass # Removed print-debug
        wizard = SetupWizard()
        
        # Interactive setup
        project_name = input("Project name: ") or "my-agent-project"
        use_case = input("Use case (customer_service/coding/research/data_analysis/custom): ") or "customer_service"
        
        config = wizard.create_project(
            name=project_name,
            use_case=use_case
        )
        
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        return 0
    
    def cmd_guardrails(self, args):
        """Security Guardrails"""
        action = args.action
        
        if action == "check":
            text = args.text or "Sample text to check"
            try:
                result = self.guardrails.check(text)
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                if result.threats:
                    pass # Removed print-debug
                    for threat in result.threats:
                        pass # Removed print-debug
            except Exception as e:
                pass # Removed print-debug
                pass # Removed print-debug
        return 0

    def cmd_security(self, args):
        """Deep Security Defense"""
        action = args.action

        if action == "deep-check":
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            sandbox_config = self.execution_sandbox.config
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pending = self.hitl_security.get_pending()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug

        elif action == "enable-deep-defense":
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug

        elif action == "audit-log":
            pass # Removed print-debug
            pass # Removed print-debug
            input_audit = self.input_validator
            output_audit = self.output_filter.get_audit_log()
            hitl_audit = self.hitl_security.get_pending()

            pass # Removed print-debug
            if output_audit:
                for i, entry in enumerate(output_audit[-5:], 1):
                    pass # Removed print-debug
            else:
                pass # Removed print-debug

            pass # Removed print-debug
            if hitl_audit:
                for req in hitl_audit[:5]:
                    pass # Removed print-debug
            else:
                pass # Removed print-debug

        elif action == "validate":
            text = args.text or "ignore previous instructions"
            pass # Removed print-debug
            result = self.input_validator.validate(text)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if result.matched_patterns:
                pass # Removed print-debug
                for p in result.matched_patterns:
                    pass # Removed print-debug
            if result.recommendations:
                pass # Removed print-debug
                for r in result.recommendations:
                    pass # Removed print-debug

        return 0

    def cmd_scale(self, args):
        """AutoScaler"""
        action = args.action
        
        if action == "status":
            status = self.autoscaler.get_status()
            pass # Removed print-debug
            pass # Removed print-debug
            for k, v in status.items():
                pass # Removed print-debug
        elif action == "scale":
            current = int(args.current or 1)
            target = self.autoscaler.calculate_target_instances(current)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
        return 0
    
    def cmd_migrate(self, args):
        """LangGraph / CrewAI Migration Tool"""
        if not args.file:
            pass # Removed print-debug
            return 1

        from_framework = getattr(args, 'from', None)
        to_framework = getattr(args, 'to', None)

        # Cross-framework migration
        if from_framework and to_framework:
            if from_framework == to_framework:
                pass # Removed print-debug
                return 1

            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug

            report = bridge_quick_convert(args.file, from_framework, to_framework)

            if report.success:
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug

                # Validate
                success, messages = self.bridge.validate_migration(
                    args.file, report.output_file,
                    f"{from_framework}_to_{to_framework}"
                )
                for msg in messages:
                    pass # Removed print-debug
            else:
                pass # Removed print-debug
                for err in report.errors:
                    pass # Removed print-debug

            return 0

        # Default: LangGraph migration (backward compatible)
        pass # Removed print-debug
        result = self.migrator.analyze_file(args.file)
        pass # Removed print-debug
        return 0
    
    def cmd_parse(self, args):
        """Structured Output Engine"""
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        return 0
    
    def cmd_pm(self, args):
        """PM Mode"""
        action = args.action
        
        if action == "report":
            report = self.pm_mode.generate_morning_report(
                sprint_name="Sprint 5",
                sprint_progress=65.0,
                completed_yesterday=["Completed login feature", "Fixed auth bug"],
                planned_today=["Develop dashboard", "API documentation"],
                blockers=["Waiting for third-party API docs"],
                velocity=42.0
            )
            pass # Removed print-debug
        elif action == "forecast":
            forecast = self.pm_mode.predict_cost(
                project_name="AI Assistant",
                current_cost=500.0,
                budget=2000.0,
                daily_burn_rate=85.0,
                days_remaining=18
            )
            pass # Removed print-debug
        elif action == "health":
            health = self.pm_mode.get_sprint_health(velocity=35, planned=50, completed=30)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
        return 0
    
    def cmd_term(self, args):
        """Terminology lookup"""
        query = args.query
        if query:
            results = self.terminology.search(query)
            if results:
                pass # Removed print-debug
                for r in results[:5]:
                    pass # Removed print-debug
                    pass # Removed print-debug
                    pass # Removed print-debug
                    pass # Removed print-debug
                    pass # Removed print-debug
            else:
                pass # Removed print-debug
        else:
            pass # Removed print-debug
        return 0
    
    def cmd_resources(self, args):
        """Resource dashboard"""
        action = args.action
        
        if action == "list":
            pass # Removed print-debug
        elif action == "stats":
            summary = self.resources.get_resource_summary()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
        elif action == "skills":
            matrix = self.resources.get_team_skills_matrix()
            pass # Removed print-debug
            for skill, members in matrix.items():
                pass # Removed print-debug
        return 0
    
    def cmd_debug(self, args):
        """Agent Debug - view traces"""
        action = args.action
        
        if action == "status":
            print(self.debugger.to_table())
        
        elif action == "stats":
            agent_id = args.agent_id
            if agent_id:
                stats = self.debugger.get_stats(agent_id)
                print(f"\n📊 Debug Stats for: {agent_id}")
                for k, v in stats.items():
                    pass # Removed print-debug
            else:
                print(self.debugger.get_stats())
        
        elif action == "list":
            agent_id = args.agent_id
            if not agent_id:
                pass # Removed print-debug
                return 1
            events = self.debugger.get_trace(agent_id, limit=args.limit or 20)
            if not events:
                pass # Removed print-debug
                return 0
            pass # Removed print-debug
            pass # Removed print-debug
            for event in events:
                pass # Removed print-debug
                if event.get('data'):
                    for k, v in event['data'].items():
                        pass # Removed print-debug
        
        elif action == "clear":
            if args.agent_id:
                self.debugger.clear(args.agent_id)
                pass # Removed print-debug
            else:
                self.debugger.clear()
                pass # Removed print-debug
        
        elif action == "enable":
            self.debugger.enable_debug()
            self.bus.enable_debug()
            print("✅ Debug mode enabled")
        
        elif action == "disable":
            self.debugger.disable_debug()
            self.bus.disable_debug()
            print("✅ Debug mode disabled")
        
        return 0
    
    def _print_impact_report(self, impact):
        """Print Impact Analysis report"""
        risk_label = "🟢 Low" if impact.risk_score < 40 else "🟡 Medium" if impact.risk_score < 70 else "🔴 High"
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        if impact.affected_tests:
            pass # Removed print-debug
            for test in impact.affected_tests:
                pass # Removed print-debug
            pass # Removed print-debug
        if impact.affected_modules:
            pass # Removed print-debug
            for mod in impact.affected_modules[:10]:  # Limit to 10
                pass # Removed print-debug
            if len(impact.affected_modules) > 10:
                pass # Removed print-debug
            pass # Removed print-debug
        if impact.recommendations:
            pass # Removed print-debug
            for rec in impact.recommendations:
                pass # Removed print-debug
            pass # Removed print-debug
    
    def _print_risk_report(self, report):
        """Print Risk Report"""
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
    
    def cmd_trace(self, args):
        """Traceability Matrix / Agent Trace"""
        action = args.action
        
        # === Traceability Matrix commands ===
        if action in ("init", "update", "report", "check"):
            return self._cmd_trace_matrix(args)
        
        # === Impact Analysis commands (no agent_id required) ===
        if action == "impact":
            if not args.file:
                pass # Removed print-debug
                return 1
            analyzer = ImpactAnalyzer(project_path=".")
            analyzer.scan_project()
            impact = analyzer.analyze_change(args.file)
            self._print_impact_report(impact)
            return 0
        
        elif action == "graphviz":
            analyzer = ImpactAnalyzer(project_path=".")
            analyzer.scan_project()
            report = analyzer.get_dependency_report()
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(report['graphviz'])
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "risk-report":
            analyzer = ImpactAnalyzer(project_path=".")
            analyzer.scan_project()
            report = analyzer.get_dependency_report()
            self._print_risk_report(report)
            return 0
        
        # === Agent Trace commands (require agent_id) ===
        agent_id = args.agent_id
        if not agent_id:
            pass # Removed print-debug
            return 1
        
        if action == "view":
            # Visualize trace
            print(self.debugger.visualize(agent_id, max_events=args.limit or 50))
        
        elif action == "correlation":
            # Visualize correlation
            correlation_id = args.correlation
            if correlation_id:
                print(self.debugger.visualize_correlation(correlation_id))
            else:
                pass # Removed print-debug
                return 1
        
        elif action == "export":
            # Export JSON
            json_data = self.debugger.export(agent_id)
            if args.output:
                with open(args.output, 'w') as f:
                    f.write(json_data)
                pass # Removed print-debug
            else:
                pass # Removed print-debug
        
        return 0
    
    def _cmd_trace_matrix(self, args):
        """Traceability Matrix - requirement tracing"""
        action = args.action
        import shutil
        from pathlib import Path
        
        if action == "init":
            # Copy template to project root
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                             "templates", "TRACEABILITY_MATRIX.md")
            dst = os.path.join(os.getcwd(), "TRACEABILITY_MATRIX.md")
            if os.path.exists(dst):
                pass # Removed print-debug
                return 1
            shutil.copy(src, dst)
            pass # Removed print-debug
            return 0
        
        elif action == "check":
            # Check traceability matrix integrity
            trace_file = Path(os.getcwd()) / "TRACEABILITY_MATRIX.md"
            if not trace_file.exists():
                pass # Removed print-debug
                pass # Removed print-debug
                return 1
            
            content = trace_file.read_text()
            
            # Statistics
            total = content.count("|")
            completed = content.count("✅")
            missing_constitution = content.count("❌")
            
            completeness = (completed / max(total, 1)) * 100
            
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            
            if missing_constitution > 0:
                pass # Removed print-debug
            
            if completeness >= 90 and missing_constitution == 0:
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        
        elif action == "report":
            pass # Removed print-debug
            return 0
        
        elif action == "update":
            if not args.id:
                pass # Removed print-debug
                return 1
            
            trace_file = Path(os.getcwd()) / "TRACEABILITY_MATRIX.md"
            if not trace_file.exists():
                pass # Removed print-debug
                return 1
            
            content = trace_file.read_text()
            # Simple replace: find row with matching ID and update status
            status_map = {
                "pending": "⏳ Pending",
                "in-progress": "🔄 In Progress",
                "completed": "✅ Done"
            }
            new_status = status_map.get(args.status, args.status or "✅ Done")
            
            # Find ID and update (simple impl: replace status in line)
            lines = content.split('\n')
            updated = False
            for i, line in enumerate(lines):
                if f"| {args.id} |" in line or f"|{args.id}|" in line:
                    # Find status column and update
                    parts = line.split('|')
                    if len(parts) >= 7:
                        parts[-2] = f" {new_status} "
                        lines[i] = '|'.join(parts)
                        updated = True
                        break
            
            if updated:
                trace_file.write_text('\n'.join(lines))
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        
        return 0

    # ─────────────────────────────────────────────────────────────────────
    # trace-check: traceability check (SAD->code / FR->test)
    # ─────────────────────────────────────────────────────────────────────

    def cmd_trace_check(self, args):
        """Traceability check

        Mode 1: --from phase2 --to phase3  (SAD -> code traceability)
                Parse SAD.md -> get FR-XX <-> module name mapping
                Parse code @FR annotation -> get FR-XX <-> code file mapping
                Output coverage matrix

        Mode 2: --from phase1 --to phase3-tests  (FR -> test traceability)
                Parse SRS.md/TRACEABILITY_MATRIX.md -> get FR-ID list
                Parse test file @covers: annotation -> get FR-ID <-> test mapping
                Output FR-ID -> test coverage
        """
        repo_path = Path(args.repo or Path.cwd())
        from_phase = args.from_phase
        to_phase = args.to_phase

        if to_phase == "phase3":
            # ── Mode 1: SAD -> code traceability ──────────────────────
            return self._trace_check_sad_to_code(repo_path)
        elif to_phase == "phase3-tests":
            # ── Mode 2: FR -> test traceability ────────────────────────
            return self._trace_check_fr_to_tests(repo_path)
        else:
            print(f"❌ Unknown --to target: {to_phase}")
            print("   Valid targets: phase3, phase3-tests")
            return 1

    def _trace_check_sad_to_code(self, repo_path: Path) -> int:
        """SAD -> code traceability check"""
        # ── Step 1: Parse SAD.md to get FR-XX → Module mapping ─────────
        sad_files = list(repo_path.glob("**/SAD.md"))
        if not sad_files:
            print("⚠️  SAD.md not found — cannot determine FR → Module mapping")
            sad_files = list(repo_path.glob("**/SAD*.md"))
        if not sad_files:
            print("❌ SAD.md not found in repo")
            return 1

        sad_path = sad_files[0]
        sad_content = sad_path.read_text(encoding="utf-8")

        # Extract FR → module path from SAD.md
        # Look for lines that contain FR-XX and a backtick path (most reliable)
        fr_module_map: Dict[str, str] = {}  # FR-ID → module_path
        for line in sad_content.split('\n'):
            if 'FR-' not in line:
                continue
            # Extract all FR references on this line
            for m in re.finditer(r'FR-(\d+)', line):
                fr_id = f"FR-{int(m.group(1)):02d}"
                if fr_id in fr_module_map:
                    continue  # already mapped
                # Try to find a backtick path on this same line
                path_m = re.search(r'`([^`]+\.py)`', line)
                if path_m:
                    fr_module_map[fr_id] = path_m.group(1).strip()

        # ── Step 2: Scan code for @FR annotations ─────────────────────
        code_files = list(repo_path.glob("**/*.py"))
        code_files = [f for f in code_files if '__pycache__' not in str(f) and 'test_' not in f.name]

        fr_code_map: Dict[str, str] = {}  # FR-ID → file_path
        for py_file in code_files:
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            # Match @FR: FR-01 or @FR:FR-01 in docstrings/comments
            for m in re.finditer(r'@FR:\s*FR-(\d+)', content):
                fr_id = f"FR-{int(m.group(1)):02d}"
                rel_path = str(py_file.relative_to(repo_path))
                fr_code_map[fr_id] = rel_path

        # ── Step 3: Build and print report ─────────────────────────────
        all_frs = sorted(fr_module_map.keys(), key=lambda x: int(x.split('-')[1]))
        matched = [(fr, fr_module_map[fr], fr_code_map.get(fr, "(missing)"))
                   for fr in all_frs]
        covered = sum(1 for fr, _, code in matched if code != "(missing)")
        total = len(all_frs)
        pct = (covered / total * 100) if total > 0 else 0

        print(f"""
## TRACEABILITY CHECK REPORT

### Mode: SAD -> code traceability
**Coverage: {pct:.1f}% ({covered}/{total})**

| FR-ID | SAD Module | Code File | Status |
|-------|---------|---------|------|""")

        for fr, module, code in matched:
            status = "✅" if code != "(missing)" else "❌"
            # Truncate module path for display
            module_short = module.split('/')[-1] if module else "-"
            print(f"| {fr} | {module_short} | {code} | {status} |")

        print()
        if covered < total:
            missing = [fr for fr, _, code in matched if code == "(missing)"]
            print(f"⚠️  FR not traced to code: {', '.join(missing)}")
            return 1
        return 0

    def _trace_check_fr_to_tests(self, repo_path: Path) -> int:
        """FR -> test traceability check"""
        # ── Step 1: Parse TRACEABILITY_MATRIX.md or SRS.md for FR list ─
        trace_files = [
            repo_path / "TRACEABILITY_MATRIX.md",
            repo_path / "SRS.md",
        ] + list(repo_path.glob("**/TRACEABILITY_MATRIX.md")) \
              + list(repo_path.glob("**/SRS.md"))

        fr_list: List[str] = []
        for tf in trace_files:
            if not tf.exists():
                continue
            try:
                content = tf.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in re.finditer(r'FR-(\d+)', content):
                fr_id = f"FR-{int(m.group(1)):02d}"
                if fr_id not in fr_list:
                    fr_list.append(fr_id)
            if fr_list:
                break  # stop at first file that has FRs

        if not fr_list:
            print("❌ No FR IDs found in TRACEABILITY_MATRIX.md or SRS.md")
            return 1

        # ── Step 2: Scan test files for @covers: annotation ───────────
        test_files = list(repo_path.glob("**/test_*.py")) \
                    + list(repo_path.glob("**/*_test.py"))

        fr_test_map: Dict[str, List[str]] = {fr: [] for fr in fr_list}
        for tf in test_files:
            try:
                content = tf.read_text(encoding="utf-8")
            except Exception:
                continue
            rel_path = str(tf.relative_to(repo_path))
            for m in re.finditer(r'@covers:\s*FR-(\d+)', content):
                fr_id = f"FR-{int(m.group(1)):02d}"
                if fr_id in fr_test_map:
                    fr_test_map[fr_id].append(f"{rel_path}")

        # ── Step 3: Build and print report ─────────────────────────────
        covered = sum(1 for fr, tests in fr_test_map.items() if tests)
        total = len(fr_list)
        pct = (covered / total * 100) if total > 0 else 0

        print(f"""
## TRACEABILITY CHECK REPORT

### Mode: FR -> test traceability
**Coverage: {pct:.1f}% ({covered}/{total})**

| FR-ID | Test Function | Status |
|-------|---------|------|""")

        for fr in sorted(fr_list, key=lambda x: int(x.split('-')[1])):
            tests = fr_test_map[fr]
            if tests:
                test_str = ", ".join(tests)
                print(f"| {fr} | {test_str} | ✅ |")
            else:
                print(f"| {fr} | (missing) | ❌ |")

        print()
        if covered < total:
            missing = [fr for fr, tests in fr_test_map.items() if not tests]
            print(f"⚠️  FR without tests: {', '.join(missing)}")
            return 1
        return 0

    def cmd_approval(self, args):
        """Human approval management"""
        action = args.action
        
        if action == "list":
            pending = self.approval_flow.get_pending()
            if not pending:
                pass # Removed print-debug
                return 0
            pass # Removed print-debug
            pass # Removed print-debug
            for p in pending:
                pass # Removed print-debug
            return 0
        
        elif action == "create":
            req_id = self.approval_flow.create_request(
                name=args.name or "New Approval Request",
                description=args.description or "",
                requester=args.requester or "user",
                requester_name=args.requester_name or "",
                approval_type=args.approval_type or "general",
            )
            pass # Removed print-debug
            return 0
        
        elif action == "approve":
            if not args.request_id:
                pass # Removed print-debug
                return 1
            level = None
            if args.level:
                try:
                    level = ApprovalLevel(args.level)
                except ValueError:
                    pass # Removed print-debug
                    return 1
            result = self.approval_flow.approve(args.request_id, args.approver or "user",
                                               comment=args.comment or "", level=level)
            if result:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "reject":
            if not args.request_id:
                pass # Removed print-debug
                return 1
            level = None
            if args.level:
                try:
                    level = ApprovalLevel(args.level)
                except ValueError:
                    pass # Removed print-debug
                    return 1
            result = self.approval_flow.reject(args.request_id, args.approver or "user",
                                              comment=args.comment or "", level=level)
            if result:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "show":
            if not args.request_id:
                pass # Removed print-debug
                return 1
            request = self.approval_flow.get_request(args.request_id)
            if not request:
                pass # Removed print-debug
                return 1
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            for i, step in enumerate(request.steps):
                marker = "→" if i == request.current_step_index else ("✓" if step.status != ApprovalStatus.PENDING else " ")
                pass # Removed print-debug
                for approval in step.approvals:
                    pass # Removed print-debug
            pass # Removed print-debug
            return 0
        
        elif action == "report":
            pass # Removed print-debug
            return 0
        
        elif action == "stats":
            stats = self.approval_flow.get_statistics()
            pass # Removed print-debug
            pass # Removed print-debug
            for k, v in stats.items():
                pass # Removed print-debug
            return 0
        
        pass # Removed print-debug
        return 1

    def cmd_risk(self, args):
        """Risk register management"""
        action = args.action

        if action == "add":
            risk = self.registry.add_risk(
                title=args.title,
                description=args.desc or "",
                level=RiskLevel.from_string(args.level) if args.level else RiskLevel.MEDIUM,
                owner=args.owner or "unassigned",
                impact=args.impact or "",
                probability=args.probability or 0.5,
                mitigation=args.mitigation or "",
            )
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif action == "list":
            level_filter = args.level
            status_filter = args.status

            if level_filter:
                risks = self.registry.get_risks_by_level(RiskLevel.from_string(level_filter))
            elif status_filter:
                risks = self.registry.get_risks_by_status(RiskStatus.from_string(status_filter))
            else:
                risks = self.registry.get_all_risks()

            if not risks:
                pass # Removed print-debug
                return 0

            pass # Removed print-debug
            pass # Removed print-debug
            for r in risks:
                pass # Removed print-debug
            return 0

        elif action == "show":
            risk = self.registry.get_risk(args.risk_id)
            if not risk:
                pass # Removed print-debug
                return 1
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if risk.mitigated_at:
                pass # Removed print-debug
            if risk.closed_at:
                pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif action == "close":
            if self.registry.update_risk_status(args.risk_id, RiskStatus.CLOSED):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif action == "mitigate":
            if self.registry.update_risk_status(args.risk_id, RiskStatus.MITIGATED):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif action == "report":
            pass # Removed print-debug
            return 0

        elif action == "stats":
            summary = self.registry.get_summary()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            for k, v in summary['by_status'].items():
                pass # Removed print-debug
            pass # Removed print-debug
            for k, v in summary['by_level'].items():
                pass # Removed print-debug
            return 0

        elif action == "delete":
            if self.registry.delete_risk(args.risk_id):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        pass # Removed print-debug
        return 1

    def cmd_roi(self, args):
        """ROI Dashboard"""
        from roi_tracker import ROICalculator

        calculator = ROICalculator()

        if args.subcommand == "dashboard":
            data = calculator.get_dashboard_data()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            for period, report in data.items():
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                if hasattr(report, 'recommendation') and report.recommendation:
                    pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif args.subcommand == "report":
            report = calculator.calculate(args.period)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

    def cmd_p2p(self, args):
        """P2P team configuration management"""
        action = args.p2p_action
        cache_path = ".p2p_team_cache.json"

        if action == "init":
            try:
                self.p2p_config = P2PTeamConfig.from_json(args.config_path)
            except FileNotFoundError as e:
                pass # Removed print-debug
                return 1
            except json.JSONDecodeError as e:
                pass # Removed print-debug
                return 1

            if not self.p2p_config.validate():
                pass # Removed print-debug
                return 1

            # Persist to cache for stateless access
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(self.p2p_config.to_dict(), f, indent=2, ensure_ascii=False)

            summary = self.p2p_config.summary()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        # Reload from cache for status/list
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    self.p2p_config = P2PTeamConfig.from_dict(json.load(f))
            except Exception:
                self.p2p_config = None

        if self.p2p_config is None:
            pass # Removed print-debug
            return 1

        if action == "status":
            summary = self.p2p_config.summary()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        if action == "list":
            members = self.p2p_config.list_agents()
            if not members:
                pass # Removed print-debug
                return 0
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            for m in members:
                spawn = "✅" if m["canSpawnSubagent"] else "❌"
                memory = "✅" if m["peerMemoryEnabled"] else "❌"
                pass # Removed print-debug
            return 0

        pass # Removed print-debug
        return 1

    def cmd_hitl(self, args):
        """HITL human intervention management"""
        action = args.action
        
        if action == "register":
            # Register owner
            owner = AgentOwner(
                owner_id=args.owner_id,
                name=args.name or "",
                email=args.email or "",
                role=args.role or "Owner",
            )
            if self.hitl_controller.register_owner(owner):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "list":
            # List owners or outputs
            if args.list_type == "owners":
                owners = self.hitl_controller.list_owners()
                if not owners:
                    pass # Removed print-debug
                    return 0
                pass # Removed print-debug
                pass # Removed print-debug
                for o in owners:
                    pass # Removed print-debug
                return 0
            elif args.list_type == "outputs":
                outputs = self.hitl_controller.get_outputs_by_status(OutputStatus(args.status_filter)) if args.status_filter else list(self.hitl_controller.outputs.values())
                if not outputs:
                    pass # Removed print-debug
                    return 0
                pass # Removed print-debug
                pass # Removed print-debug
                for o in outputs:
                    pass # Removed print-debug
                return 0
            elif args.list_type == "pending":
                pending = self.hitl_controller.get_pending_reviews(args.owner_filter)
                if not pending:
                    pass # Removed print-debug
                    return 0
                pass # Removed print-debug
                pass # Removed print-debug
                for o in pending:
                    pass # Removed print-debug
                return 0
        
        elif action == "assign":
            # Assign Agent to owner
            if self.hitl_controller.assign_agent_to_owner(args.agent_id, args.owner_id):
                owner = self.hitl_controller.get_owner(args.owner_id)
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "approve":
            # Approve output
            if self.hitl_controller.approve_output(args.output_id, args.approver or "user"):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "reject":
            # Request revision
            feedback = args.feedback or "Revision requested"
            if self.hitl_controller.request_revision(args.output_id, args.approver or "user", feedback):
                pass # Removed print-debug
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        elif action == "show":
            # Show output details
            output = self.hitl_controller.get_output(args.output_id)
            if not output:
                pass # Removed print-debug
                return 1
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if output.submitted_at:
                pass # Removed print-debug
            if output.approved_at:
                pass # Removed print-debug
            if output.feedback:
                pass # Removed print-debug
            if output.revision_count > 0:
                pass # Removed print-debug
            pass # Removed print-debug
            return 0
        
        elif action == "stats":
            # Show statistics
            stats = self.hitl_controller.get_statistics()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            for status, count in stats['by_status'].items():
                pass # Removed print-debug
            return 0
        
        elif action == "report":
            # Generate report
            pass # Removed print-debug
            return 0
        
        elif action == "escalate":
            # Escalate output
            from hitl_controller import EscalationLevel
            level = EscalationLevel(args.level) if args.level else EscalationLevel.OWNER
            if self.hitl_controller.escalate_output(args.output_id, args.reason, level):
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0
        
        pass # Removed print-debug
        return 1

    def cmd_constitution(self, args):
        """Constitution management"""
        subcmd = args.subcommand

        if subcmd == "view":
            from constitution import load_constitution
            pass # Removed print-debug
        elif subcmd == "thresholds":
            from constitution import get_quality_thresholds
            thresholds = get_quality_thresholds()
            pass # Removed print-debug
            for k, v in thresholds.items():
                pass # Removed print-debug
        elif subcmd == "errors":
            from constitution import get_error_levels
            levels = get_error_levels()
            pass # Removed print-debug
            for k, v in levels.items():
                pass # Removed print-debug
        elif subcmd == "check":
            skip_failed = getattr(args, 'skip_failed', False)
            auto_fix = getattr(args, 'auto_fix', False)

            # Constitution check is the frontend interface for framework Constitution validation
            # --auto-fix / --skip-failed options reserved (for phase-level Constitution)
            # For Constitution validation use: python cli.py enforce --level BLOCK
            if auto_fix or skip_failed:
                print("⚠️  --auto-fix / --skip-failed for constitution check is in development.")
                print("   For Constitution validation use: python cli.py enforce --level BLOCK")
                return 0

            # Constitution check framework
            from pathlib import Path
            project_root = Path(getattr(args, 'project', '.'))
            phase_type = getattr(args, 'type', None)
            if not phase_type:
                print("❌ constitution check requires --type argument (e.g.: srs, sad, test_plan, implementation)")
                return 1

            # Use validate_constitution_compliance (interface provided by constitution module)
            try:
                from constitution import validate_constitution_compliance
                result = validate_constitution_compliance(str(project_root))
                compliant = result.get("compliant", False)
                checks = result.get("checks", [])
                icon = "✅" if compliant else "❌"
                print(f"\n{icon} Constitution check ({phase_type}): compliant={compliant}")
                if checks:
                    for check in checks[:10]:
                        print(f"   - {check}")
            except Exception as e:
                print(f"❌ Constitution check failed: {e}")
                return 1
            return 0
        elif subcmd == "edit":
            pass # Removed print-debug
            import subprocess
            subprocess.run(["open", "constitution/CONSTITUTION.md"])
        elif subcmd == "compile":
            from constitution import compile_constitution
            compiled = compile_constitution()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
        elif subcmd == "verify":
            if not args.output:
                pass # Removed print-debug
                pass # Removed print-debug
                return 1
            from constitution import compile_constitution, verify_agent_output
            compiled = compile_constitution()
            result = verify_agent_output(compiled, args.output)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if result['violations']:
                pass # Removed print-debug
                for v in result['violations']:
                    pass # Removed print-debug
        else:
            pass # Removed print-debug
            pass # Removed print-debug
        return 0

    def cmd_constitution_sync(self, args):
        """Sync Constitution to Policy Engine"""
        from enforcement.constitution_policy_sync import ConstitutionPolicyGenerator
        
        generator = ConstitutionPolicyGenerator()
        policies = generator.sync()
        
        pass # Removed print-debug
        return 0

    def cmd_spec_track(self, args):
        """Spec Tracking - specification tracking"""
        from quality_gate.spec_tracking_checker import SpecTrackingChecker
        
        action = args.action
        project_root = os.getcwd()
        
        checker = SpecTrackingChecker(project_root)
        
        if action == "init":
            # Copy template to project root
            import shutil
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                             "templates", "SPEC_TRACKING.md")
            dst = os.path.join(project_root, "SPEC_TRACKING.md")
            
            if os.path.exists(dst):
                pass # Removed print-debug
                return 1
            
            shutil.copy(src, dst)
            pass # Removed print-debug
            return 0
        
        elif action == "check":
            exists = checker.check_exists()
            if not exists:
                pass # Removed print-debug
                return 1
            
            completeness = checker.check_completeness()
            if completeness.get("complete"):
                pass # Removed print-debug
                return 0
            else:
                missing = completeness.get("missing", [])
                for item in missing:
                    pass # Removed print-debug
                return 1
        
        elif action == "report":
            result = checker.run()
            checker.print_report()
            return 0
        
        return 0

    def cmd_version(self, args):
        """Show version"""
        pass # Removed print-debug
        return 0

    def cmd_gatekeeper(self, args):
        """Workflow Gatekeeper"""
        from anti_shortcut.gatekeeper import Gatekeeper, Phase
        
        gk = Gatekeeper()
        action = args.action
        
        if action == "status":
            gk.print_status()
        elif action == "check":
            pass # Removed print-debug
            all_passed = gk.check_all_gates()
            gk.print_status()
            if all_passed:
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        elif action == "enforce":
            pass # Removed print-debug
            gk.start_phase(Phase.CONSTITUTION)
            # Run Constitution gates
            for gate in gk.phase_records[Phase.CONSTITUTION].gates:
                gk.check_gate(gate.gate_id)
            gk.print_status()
            if gk.can_proceed_to_next_phase():
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        return 0

    def cmd_confirmations(self, args):
        """Double Confirmation Management"""
        dc = DoubleConfirmation(timeout_minutes=30)
        action = args.action
        
        if action == "list":
            pending = dc.get_pending(operation=args.operation)
            if not pending:
                pass # Removed print-debug
                return 0
            pass # Removed print-debug
            for p in pending:
                required = 1 if p.level == ConfirmationLevel.SINGLE else 2
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
            return 0
        
        elif action == "confirm":
            if not args.confirmation_id or not args.confirmed_by:
                pass # Removed print-debug
                return 1
            success = dc.confirm(args.confirmation_id, args.confirmed_by)
            if success:
                status = dc.get_status(args.confirmation_id)
                if status and status.get("status") == "approved":
                    pass # Removed print-debug
                else:
                    pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        
        elif action == "reject":
            if not args.confirmation_id or not args.confirmed_by:
                pass # Removed print-debug
                return 1
            success = dc.reject(args.confirmation_id, args.confirmed_by, args.reason or "")
            if success:
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        
        elif action == "status":
            if not args.confirmation_id:
                pass # Removed print-debug
                return 1
            status = dc.get_status(args.confirmation_id)
            if status:
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                if 'required' in status:
                    pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        
        elif action == "check":
            if not args.operation:
                pass # Removed print-debug
                return 1
            level = dc.requires_confirmation(args.operation)
            pass # Removed print-debug
            if level == ConfirmationLevel.BLOCKED:
                pass # Removed print-debug
            elif level == ConfirmationLevel.APPROVAL:
                pass # Removed print-debug
            elif level == ConfirmationLevel.DOUBLE:
                pass # Removed print-debug
            elif level == ConfirmationLevel.SINGLE:
                pass # Removed print-debug
            return 0
        
        return 0

    def cmd_release(self, args):
        """Release Management with Double Confirmation"""
        if not args.version:
            pass # Removed print-debug
            return 1
        
        dc = DoubleConfirmation(timeout_minutes=30)
        operation = "release"
        description = f"Release version {args.version} to GitHub"
        metadata = {"version": args.version, "repo": args.repo or "main"}
        
        level = dc.requires_confirmation(operation)
        
        if level == ConfirmationLevel.BLOCKED:
            pass # Removed print-debug
            return 1
        
        if level == ConfirmationLevel.NONE:
            pass # Removed print-debug
            return 0
        
        if not args.confirm:
            pass # Removed print-debug
            pass # Removed print-debug
            conf_id = dc.create_pending(operation, description, metadata)
            if conf_id:
                pass # Removed print-debug
            return 1
        
        # Create pending confirmation
        conf_id = dc.create_pending(operation, description, metadata)
        
        if conf_id == "__BLOCKED__":
            pass # Removed print-debug
            return 1
        
        # Agent confirmation (first pass)
        pass # Removed print-debug
        dc.confirm(conf_id, confirmed_by="agent-cli")
        
        pending = dc._find_pending(conf_id)
        if pending:
            required = 1 if pending.level == ConfirmationLevel.SINGLE else 2
            current = len(pending.confirmations)
            pass # Removed print-debug
            
            if current < required:
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
                return 1
        else:
            status = dc.get_status(conf_id)
            if status and status.get("status") == "approved":
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        
        return 0

    def cmd_policy(self, args):
        """Run Policy Engine checks"""
        from enforcement.policy_engine import create_hard_block_engine
        try:
            engine = create_hard_block_engine()
            results = engine.enforce_all()
            summary = engine.get_summary()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if summary['all_passed']:
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1
        except Exception as e:
            pass # Removed print-debug
            return 1

    def cmd_install_hook(self, args):
        """Install pre-commit hook"""
        import shutil
        from pathlib import Path
        
        # Try new template location first, fall back to old
        hook_source = Path(__file__).parent / "templates" / "pre-commit-hook.sh"
        if not hook_source.exists():
            hook_source = Path(__file__).parent / "pre-commit.template"
        
        if not hook_source.exists():
            pass # Removed print-debug
            return 1
        
        hook_dest = Path(".git") / "hooks" / "pre-commit"
        
        if hook_dest.exists() and not args.force:
            pass # Removed print-debug
            return 1
        
        shutil.copy(hook_source, hook_dest)
        hook_dest.chmod(0o755)
        pass # Removed print-debug
        return 0

    def cmd_enforcement_config(self, args):
        """Unified Enforcement Configuration"""
        from enforcement_config import EnforcementConfig, ConfigGenerator, EnforcementMode
        
        action = args.action
        
        if not action or action == "show":
            config = EnforcementConfig.load()
            pass # Removed print-debug
        elif action == "init":
            config = ConfigGenerator.local_only()
            config.save()
            pass # Removed print-debug
            pass # Removed print-debug
        elif action == "set":
            mode = args.mode or "local"
            if mode == "github":
                config = ConfigGenerator.github_actions()
            elif mode == "gitlab":
                config = ConfigGenerator.gitlab_ci()
            elif mode == "jenkins":
                config = ConfigGenerator.jenkins()
            elif mode == "azure":
                config = ConfigGenerator.azure_pipelines()
            else:
                config = ConfigGenerator.local_only()
            config.save()
            pass # Removed print-debug
            pass # Removed print-debug
        elif action == "detect":
            config = ConfigGenerator.auto_detect()
            pass # Removed print-debug
            pass # Removed print-debug
        else:
            pass # Removed print-debug
            return 1
        return 0

    def cmd_enforcement(self, args):
        """
        Enforcement unified CLI

        Subcommands:
        - run: run all enforcement checks
        - check: check enforcement status
        - status: show summary
        - install: install hook
        - config: show/set enforcement configuration
        """
        from enforcement_config import EnforcementConfig, ConfigGenerator
        from enforcement import PolicyEngine, ConstitutionAsCode, EnforcementLevel
        from enforcement.execution_registry import ExecutionRegistry

        sub = args.subcommand

        if sub == "run":
            # Run all enforcement checks
            pass # Removed print-debug
            pass # Removed print-debug

            passed = 0
            failed = 0

            # 1. Policy Engine
            pass # Removed print-debug
            try:
                engine = PolicyEngine()
                results = engine.enforce_all()
                summary = engine.get_summary()
                pass # Removed print-debug
                if summary['all_passed']:
                    passed += 1
                    pass # Removed print-debug
                else:
                    failed += 1
                    pass # Removed print-debug
            except Exception as e:
                failed += 1
                pass # Removed print-debug

            # 2. Constitution Check
            pass # Removed print-debug
            try:
                constitution = ConstitutionAsCode()
                # Try reading commit message from environment variable
                import os
                commit_msg = os.environ.get('COMMIT_MSG', os.environ.get('GIT_COMMITMSG', ''))
                if commit_msg:
                    constitution.enforce({"commit_message": commit_msg})
                pass # Removed print-debug
                passed += 1
            except Exception as e:
                failed += 1
                pass # Removed print-debug

            # 3. Record to Registry
            pass # Removed print-debug
            try:
                registry = ExecutionRegistry()
                registry.record("enforcement-run", {
                    "policy_passed": passed > 0,
                    "constitution_passed": True,
                })
                pass # Removed print-debug
            except Exception as e:
                pass # Removed print-debug

            # Summary
            pass # Removed print-debug
            if failed == 0:
                pass # Removed print-debug
                return 0
            else:
                pass # Removed print-debug
                return 1

        elif sub == "check":
            # Check enforcement status
            config = EnforcementConfig.load()
            pass # Removed print-debug
            return 0

        elif sub == "status":
            # Show summary
            config = EnforcementConfig.load()
            pass # Removed print-debug

            # Show Policy Engine status
            pass # Removed print-debug
            try:
                engine = PolicyEngine()
                summary = engine.get_summary()
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug
            except Exception as e:
                pass # Removed print-debug
            return 0

        elif sub == "install":
            # Install hook
            from pathlib import Path
            import shutil

            hook_source = Path(__file__).parent / "pre-commit.template"
            hook_dest = Path(__file__).parent / ".git" / "hooks" / "pre-commit"

            if not hook_source.exists():
                pass # Removed print-debug
                return 1

            hook_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(hook_source, hook_dest)
            hook_dest.chmod(0o755)

            pass # Removed print-debug
            return 0

        elif sub == "config":
            # Show/set enforcement configuration
            if args.mode:
                mode = args.mode
                if mode == "local":
                    config = ConfigGenerator.local_only()
                elif mode == "github":
                    config = ConfigGenerator.github_actions()
                elif mode == "gitlab":
                    config = ConfigGenerator.gitlab_ci()
                elif mode == "jenkins":
                    config = ConfigGenerator.jenkins()
                elif mode == "azure":
                    config = ConfigGenerator.azure_pipelines()
                else:
                    pass # Removed print-debug
                    pass # Removed print-debug
                    return 1

                config.save()
                pass # Removed print-debug

            pass # Removed print-debug
            return 0

        else:
            pass # Removed print-debug
            pass # Removed print-debug
            return 1

    def _run_phase_enforcer_check(self):
        """
        PhaseEnforcer automated check (BUG-001 fix)
        
        No daemon dependency; checked directly at CLI execution time.
        This allows Quality Gate to auto-trigger in environments without a daemon.
        """
        from enforcement.framework_enforcer import FrameworkEnforcer
        
        pass # Removed print-debug
        
        enforcer = FrameworkEnforcer(os.getcwd())
        result = enforcer.run(level="BLOCK")
        
        phase_passed = True
        if result.violations:
            phase_passed = False
            for msg, fix in result.violations:
                pass # Removed print-debug
                if fix:
                    pass # Removed print-debug
        
        if result.warnings:
            for msg, fix in result.warnings:
                pass # Removed print-debug
        
        if phase_passed:
            pass # Removed print-debug
        
        return result.passed

    def cmd_quality_gate(self, args):
        """Quality Gate - unified quality gate check"""
        from quality_gate import UnifiedGate
        from quality_gate.spec_tracking_checker import SpecTrackingChecker

        sub = args.subcommand

        if sub == "check" or sub == "all":
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug

            # ========================================
            # v6.05: enforce BLOCK level (anti-spoofing mechanism 1)
            # ========================================
            from enforcement.framework_enforcer import FrameworkEnforcer
            
            enforcer = FrameworkEnforcer(os.getcwd())
            result = enforcer.run(level="BLOCK")
            
            # #5 fix: record FrameworkEnforcer result to DEVELOPMENT_LOG
            try:
                log_path = os.path.join(os.getcwd(), "DEVELOPMENT_LOG.md")
                import datetime
                timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                status_icon = "✅" if result.passed else "❌"
                violations_count = len(result.violations)
                log_entry = f"\n{status_icon} **[{timestamp}] FrameworkEnforcer BLOCK**: {status_icon} {violations_count} violations\n"
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
            except Exception as e:
                pass  # do not block main flow
            
            # Block on failure
            if not result.passed:
                print(f"❌ FrameworkEnforcer: {len(result.violations)} violations")
                for msg, fix in result.violations[:5]:
                    print(f"   - {msg}")
                sys.exit(1)
            
            pass # Removed print-debug

            gate = UnifiedGate()
            result = gate.check_all()

            pass # Removed print-debug
            pass # Removed print-debug

            pass # Removed print-debug
            for check in result.checks:
                status = "✅" if check.passed else "❌"
                pass # Removed print-debug
                if check.violations:
                    for v in check.violations[:3]:
                        pass # Removed print-debug

            # Framework Enforcement - SPEC_TRACKING
            pass # Removed print-debug
            project_root = os.getcwd()
            checker = SpecTrackingChecker(project_root)
            spec_result = checker.run_enforcement()

            if not spec_result.get('exists', False):
                pass # Removed print-debug
                pass # Removed print-debug
                sys.exit(1)
            else:
                completeness = spec_result.get('completeness', 0)
                if completeness < 90:
                    pass # Removed print-debug
                    sys.exit(1)
                pass # Removed print-debug
                if spec_result.get('missing'):
                    for m in spec_result['missing'][:3]:
                        pass # Removed print-debug

            sys.exit(0 if result.passed else 1)

        elif sub == "doc" or sub == "docs":
            gate = UnifiedGate()
            result = gate.check_documents_only()
            pass # Removed print-debug
            if result.violations:
                for v in result.violations:
                    pass # Removed print-debug
            sys.exit(0 if result.passed else 1)

        elif sub == "constitution":
            gate = UnifiedGate()
            result = gate.check_constitution_only()
            pass # Removed print-debug
            if result.violations:
                for v in result.violations[:5]:
                    pass # Removed print-debug
            sys.exit(0 if result.passed else 1)

        elif sub == "phase":
            gate = UnifiedGate()
            result = gate.check_phase_only()
            pass # Removed print-debug
            if result.violations:
                for v in result.violations:
                    pass # Removed print-debug
            sys.exit(0 if result.passed else 1)

        elif sub == "aspice":
            # ASPICE compliance check (using Document Existence)
            gate = UnifiedGate()
            result = gate.check_documents_only()
            pass # Removed print-debug
            sys.exit(0 if result.passed else 1)

        elif sub == "ai-test":
            # AI Test Suite Generator
            import subprocess
            from pathlib import Path

            cli_path = Path(__file__).parent / "quality_gate" / "ai_test_suite" / "cli.py"
            if not cli_path.exists():
                pass # Removed print-debug
                return 1

            cmd = [sys.executable, str(cli_path)]

            target = args.target
            if not target:
                # Default: use app/ or src/ as target
                if os.path.exists("app"):
                    target = "app"
                elif os.path.exists("src"):
                    target = "src"
                else:
                    pass # Removed print-debug
                    return 1

            cmd.extend(["-t", target])

            if args.output:
                cmd.extend(["-o", args.output])

            if args.model:
                cmd.extend(["-m", args.model])

            if args.context:
                for ctx in args.context:
                    cmd.extend(["-c", ctx])

            pass # Removed print-debug

            result = subprocess.run(cmd, cwd=os.getcwd())
            return result.returncode

        else:
            pass # Removed print-debug
            pass # Removed print-debug
            return 1

        return 0

    def cmd_enforce(self, args):
        """Framework Enforcement - unified execution of all enforcement"""
        from enforcement.framework_enforcer import FrameworkEnforcer

        level = args.level
        project_root = args.project
        skip_failed = getattr(args, 'skip_failed', False)
        auto_fix = getattr(args, 'auto_fix', False)

        # Handle --aspice-report first
        if getattr(args, 'aspice_report', False):
            enforcer = FrameworkEnforcer(project_root)
            report = enforcer.generate_aspice_report()
            pass # Removed print-debug
            return 0

        pass # Removed print-debug

        enforcer = FrameworkEnforcer(project_root)
        result = enforcer.run(level=level)

        # #5 fix: record FrameworkEnforcer result to DEVELOPMENT_LOG
        try:
            log_path = os.path.join(project_root, "DEVELOPMENT_LOG.md")
            import datetime
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            status_icon = "✅" if result.passed else "❌"
            violations_count = len(result.violations)
            log_entry = f"\n{status_icon} **[{timestamp}] FrameworkEnforcer {level}**: {status_icon} {violations_count} violations\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            pass  # do not block main flow

        violations = result.violations
        warnings = result.warnings

        # --auto-fix: auto-fix violations that have a fix
        if auto_fix and violations:
            from enforcement.framework_enforcer import AutoFixRegistry
            fix_registry = AutoFixRegistry()
            fixed_count = 0
            for msg, fix in violations:
                if fix:
                    try:
                        fix_registry.apply_fix(fix)
                        fixed_count += 1
                    except Exception as e:
                        pass # Removed print-debug
            if fixed_count > 0:
                pass # Removed print-debug

        # --skip-failed: report only passing results
        if skip_failed and violations:
            violations = []
            if not warnings:
                result.passed = True

        if violations:
            pass # Removed print-debug
            for msg, fix in violations:
                pass # Removed print-debug
                if fix:
                    pass # Removed print-debug
        else:
            pass # Removed print-debug

        if warnings:
            pass # Removed print-debug
            for msg, fix in warnings:
                pass # Removed print-debug
                if fix:
                    pass # Removed print-debug
        else:
            pass # Removed print-debug

        if result.passed:
            pass # Removed print-debug
            return 0
        else:
            pass # Removed print-debug
            return 1

    def cmd_decision_gate(self, args):
        """Decision Gate - decision classification gate"""
        from decision_gate import DecisionRecorder

        sub = args.subcommand
        cmd_args = args.args or []

        recorder = DecisionRecorder()

        if sub == "classify" or sub == "add":
            # Add and classify decision
            if len(cmd_args) < 2:
                pass # Removed print-debug
                sys.exit(1)

            item = cmd_args[0]
            description = cmd_args[1]
            spec_ref = cmd_args[2] if len(cmd_args) > 2 else None

            result = recorder.classify_and_record(item, description, spec_ref)

            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if result.requires_confirmation:
                pass # Removed print-debug
            if result.options:
                pass # Removed print-debug
            if result.recommendation:
                pass # Removed print-debug

        elif sub == "list" or sub == "ls":
            decisions = recorder.get_all()
            if not decisions:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
                pass # Removed print-debug
                for d in decisions:
                    pass # Removed print-debug

        elif sub == "pending" or sub == "p":
            pending = recorder.get_pending()
            pass # Removed print-debug
            for d in pending:
                pass # Removed print-debug

        elif sub == "confirm":
            if len(cmd_args) < 2:
                pass # Removed print-debug
                sys.exit(1)

            decision_id = cmd_args[0]
            value = cmd_args[1]
            recorder.confirm(decision_id, value)
            pass # Removed print-debug

        elif sub == "report":
            pass # Removed print-debug

        else:
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug

        return 0

    def cmd_agent_proof_hook(self, args):
        """Agent-Proof Hook Management"""
        from enforcement.agent_proof_hook import AgentProofHook

        hook = AgentProofHook()

        if args.action == "install":
            hook.install(force=True)
        elif args.action == "verify":
            if not hook.verify():
                sys.exit(1)
        elif args.action == "uninstall":
            hook.uninstall()
        else:
            hook.install()

        return 0

    def cmd_memory(self, args):
        """Memory Governance CLI"""
        from memory_governance import (
            MemoryValidator,
            StateCoordinator,
            ConflictResolver,
            MemoryAudit,
            ResolutionStrategy
        )

        sub = args.subcommand

        if sub == "validate":
            # Verify memory
            validator = MemoryValidator()
            pass # Removed print-debug
            return 0

        elif sub == "status":
            # Show coordination status
            coordinator = StateCoordinator()
            summary = coordinator.get_global_state_summary()
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif sub == "audit":
            # Show audit log
            audit = MemoryAudit()
            records = audit.get_records(limit=10)
            pass # Removed print-debug
            for r in records:
                pass # Removed print-debug
            return 0

        elif sub == "resolve":
            # Resolve conflict
            pass # Removed print-debug
            return 0

        return 0

    def cmd_m27(self, args):
        """M2.7 Self-Evolving Integration"""
        from m27_integration import (
            HybridAttention,
            SelfIteration,
            FailureAnalyzer,
            HarnessOptimizer,
            AttentionConfig
        )

        sub = args.sub

        if sub == "status":
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif sub == "analyze":
            log = args.failure_log or "No failure log provided"
            analyzer = FailureAnalyzer()
            path = analyzer.analyze(log)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            for r in path.recommendations:
                pass # Removed print-debug
            return 0

        elif sub == "iterate":
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif sub == "optimize":
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        return 0

    def cmd_metrics(self, args):
        """Code Metrics - code quality metrics"""
        from code_metrics import MetricsTracker

        sub = args.subcommand if hasattr(args, 'subcommand') else "report"

        tracker = MetricsTracker()

        if sub == "report" or sub == "check":
            pass # Removed print-debug
            report = tracker.generate_report()

            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug

            if report['complexity_violations']:
                pass # Removed print-debug
                for v in report['complexity_violations'][:10]:
                    pass # Removed print-debug

            if report.get('coupling'):
                pass # Removed print-debug
                pass # Removed print-debug
                pass # Removed print-debug

            pass # Removed print-debug
            return 0

        elif sub == "history":
            latest = tracker.get_latest()
            if latest:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        return 0

    def cmd_debt(self, args):
        """Technical Debt - technical debt tracking"""
        from technical_debt import DebtRegistry

        sub = args.subcommand if hasattr(args, 'subcommand') else "list"
        action_args = args.args if hasattr(args, 'args') else []

        registry = DebtRegistry()

        if sub == "add":
            if len(action_args) < 1:
                pass # Removed print-debug
                return 1

            description = action_args[0]
            severity = args.severity if hasattr(args, 'severity') and args.severity else "medium"
            ticket = args.ticket if hasattr(args, 'ticket') and args.ticket else None

            debt_id = registry.add(description, severity, ticket)
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            if ticket:
                pass # Removed print-debug
            return 0

        elif sub == "list" or sub == "ls":
            debts = registry.list_all()
            if not debts:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
                pass # Removed print-debug
                for d in debts:
                    pass # Removed print-debug
            return 0

        elif sub == "open":
            debts = registry.list_open()
            pass # Removed print-debug
            for d in debts:
                pass # Removed print-debug
            return 0

        elif sub == "resolve":
            if len(action_args) < 1:
                pass # Removed print-debug
                return 1

            debt_id = action_args[0]
            if registry.resolve(debt_id):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif sub == "accept":
            if len(action_args) < 2:
                pass # Removed print-debug
                return 1

            debt_id = action_args[0]
            reason = action_args[1]
            if registry.accept(debt_id, reason):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif sub == "report":
            pass # Removed print-debug
            return 0

        return 0

    def cmd_adr(self, args):
        """ADR - Architecture Decision Records"""
        from adr import ADR, ADRRegistry

        sub = args.adr_action if hasattr(args, 'adr_action') else "list"
        action_args = args.args if hasattr(args, 'args') else []

        registry = ADRRegistry()

        if sub == "create" or sub == "new":
            title = action_args[0] if action_args else "(no title)"

            adr = ADR(
                title=title,
                context="(describe context)",
                decision="(describe decision)",
                consequences="(describe consequences)"
            )

            adr_id = registry.save(adr)
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif sub == "list" or sub == "ls":
            adrs = registry.list_all()
            if not adrs:
                pass # Removed print-debug
                pass # Removed print-debug
            else:
                pass # Removed print-debug
                pass # Removed print-debug
                for adr in adrs:
                    pass # Removed print-debug
            return 0

        elif sub == "get" or sub == "show":
            adr_id = action_args[0] if action_args else None
            if not adr_id:
                pass # Removed print-debug
                return 1

            adr = registry.get(adr_id)
            if adr:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif sub == "status":
            adr_id = action_args[0] if len(action_args) > 0 else None
            new_status = action_args[1] if len(action_args) > 1 else None
            if not adr_id or not new_status:
                pass # Removed print-debug
                return 1

            if registry.update_status(adr_id, new_status):
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif sub == "export":
            count = registry.export_markdown("docs/adr/")
            pass # Removed print-debug
            return 0

        elif sub == "report":
            pass # Removed print-debug
            return 0

        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        return 0

    def cmd_guide(self, args):
        """Learning Guide - learning guide"""
        from pathlib import Path
        
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        pass # Removed print-debug
        return 0

    def cmd_persona(self, args):
        """Agent Persona - persona management"""
        from agent_personas import generate_persona_prompt

        action = args.action

        if action == "list":
            pass # Removed print-debug
            pass # Removed print-debug
            personas = ["architect", "developer", "reviewer", "qa", "pm", "devops"]
            for p in personas:
                prompt = generate_persona_prompt(p)
                pass # Removed print-debug
                pass # Removed print-debug
            return 0

        elif action == "show":
            if not args.persona_type:
                pass # Removed print-debug
                return 1
            prompt = generate_persona_prompt(args.persona_type)
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif action == "apply":
            if not args.persona_type:
                pass # Removed print-debug
                return 1
            # Show usage examples
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        return 0

    def cmd_roadmap(self, args):
        """Learning Roadmap - learning roadmap"""
        from pathlib import Path
        
        roadmap = Path("docs/ROADMAP.md")
        if roadmap.exists():
            pass # Removed print-debug
        else:
            pass # Removed print-debug
        return 0

    def cmd_ralph(self, args):
        """Ralph Mode - long-running task monitoring"""
        # Proxy to RalphCLI
        # Reconstruct args to match RalphCLI format
        import argparse as _argparse
        
        # Handle ralph subcommands
        ralph_args = args.ralph_command
        
        # Extract task_id (if present)
        task_id = None
        interval = 30
        background = False
        phase = None
        target_phase = None
        status_filter = None
        
        if ralph_args:
            # start task_id [--interval N] [--background]
            if ralph_args[0] == "start" and len(ralph_args) > 1:
                task_id = ralph_args[1]
                # Parse optional parameters
                for i in range(2, len(ralph_args)):
                    if ralph_args[i] == "--interval" or ralph_args[i] == "-i":
                        if i + 1 < len(ralph_args):
                            interval = int(ralph_args[i + 1])
                    elif ralph_args[i] == "--background" or ralph_args[i] == "-b":
                        background = True
            elif ralph_args[0] == "status" and len(ralph_args) > 1:
                task_id = ralph_args[1]
            elif ralph_args[0] == "stop" and len(ralph_args) > 1:
                task_id = ralph_args[1]
            elif ralph_args[0] == "init" and len(ralph_args) > 1:
                task_id = ralph_args[1]
                for i in range(2, len(ralph_args)):
                    if ralph_args[i] == "--phase" or ralph_args[i] == "-p":
                        if i + 1 < len(ralph_args):
                            phase = ralph_args[i + 1]
            elif ralph_args[0] == "advance" and len(ralph_args) > 1:
                task_id = ralph_args[1]
                for i in range(2, len(ralph_args)):
                    if ralph_args[i] == "--to" or ralph_args[i] == "-t":
                        if i + 1 < len(ralph_args):
                            target_phase = ralph_args[i + 1]
            elif ralph_args[0] == "list":
                for i in range(1, len(ralph_args)):
                    if ralph_args[i] == "--status" or ralph_args[i] == "-s":
                        if i + 1 < len(ralph_args):
                            status_filter = ralph_args[i + 1]
        
        # Build namespace
        ralph_namespace = _argparse.Namespace()
        ralph_namespace.command = ralph_args[0] if ralph_args else "list"
        ralph_namespace.task_id = task_id
        ralph_namespace.interval = interval
        ralph_namespace.background = background
        ralph_namespace.phase = phase
        ralph_namespace.to = target_phase
        ralph_namespace.status = status_filter
        
        return self.ralph_cli.run(ralph_namespace)

    def cmd_stage_pass(self, args):
        """STAGE_PASS Generator - integrated quality certification"""
        from quality_gate.stage_pass_generator import IntegratedStagePassGenerator

        phase = args.phase
        project_dir = args.project

        generator = IntegratedStagePassGenerator(project_dir, phase)

        # --skip-failed: filter out failed checks
        if getattr(args, 'skip_failed', False):
            generator.skip_failed = True

        # --auto-fix: auto-fix known issues
        if getattr(args, 'auto_fix', False):
            generator.auto_fix = True

        success = generator.run()

        return 0 if success else 1

    def cmd_phase_truth(self, args):
        """Phase truth verification - verify whether a Phase truly passed"""
        from quality_gate.phase_truth_verifier import PhaseTruthVerifier

        phase = args.phase
        project_dir = args.project

        verifier = PhaseTruthVerifier(project_dir, phase)
        result = verifier.verify()

        return 0 if result["passed"] else 1

    def cmd_skill_check(self, args):
        """SKILL.md mandatory read check"""
        from quality_gate.skill_preheater import SkillPreheater
        from quality_gate.skill_interrogator import SkillInterrogator
        from quality_gate.citation_enforcer import CitationEnforcer
        
        phase = args.phase if hasattr(args, 'phase') and args.phase else 1
        
        if args.mode == "preheat":
            preheater = SkillPreheater()
            result = preheater.run_preflight(phase)
            pass # Removed print-debug
        elif args.mode == "interrogate":
            interrogator = SkillInterrogator()
            result = interrogator.run_interrogation(phase)
            pass # Removed print-debug
        elif args.mode == "citation":
            enforcer = CitationEnforcer()
            result = enforcer.run_citation_check(phase)
            pass # Removed print-debug
        else:
            # Default: run all checks
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            
            pass # Removed print-debug
            preheater = SkillPreheater()
            preheater.run_preflight(phase)
            
            pass # Removed print-debug
            interrogator = SkillInterrogator()
            interrogator.run_interrogation(phase)
            
            pass # Removed print-debug
            enforcer = CitationEnforcer()
            enforcer.run_citation_check(phase)
        
        return 0

    def cmd_model_recommend(self, args):
        """Phase -> Model recommendation

        Read .methodology/state.json to get current_phase,
        Use Multi-Provider ModelRouter to output model recommendation.
        """
        from pathlib import Path
        from smart_router import route_by_phase
        from provider_abstraction import ModelRouter

        repo_path = args.repo or "."
        state_path = Path(repo_path) / ".methodology" / "state.json"

        # Read state.json to get current_phase
        phase = args.phase
        if phase is None:
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text())
                    phase = state.get("current_phase")
                except (json.JSONDecodeError, IOError) as e:
                    print(f"❌ Failed to read state.json: {e}")
                    return 1
            else:
                print(f"❌ state.json not found at {state_path}")
                return 1

        if phase is None:
            print("❌ current_phase is None in state.json")
            return 1

        # Use Multi-Provider ModelRouter
        task_hint = getattr(args, 'task_hint', None)
        state_path_str = str(state_path) if state_path.exists() else None

        if getattr(args, 'provider', False):
            # JSON output mode
            info = ModelRouter().route_with_info(
                phase=phase,
                task_hint=task_hint,
                state_path=state_path_str
            )
            print(json.dumps(info, indent=2))
            return 0

        # Default human-readable output
        result = route_by_phase(phase, state_path_str, task_hint)

        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Phase {phase} -> Model Recommendation                          ║
╠══════════════════════════════════════════════════════════════╣""")
        print(f"║  Model:        {result.model:<45}║")
        print(f"║  Provider:     {result.provider:<45}║")
        print(f"║  Est. Cost:    ${result.estimated_cost:.4f}                                     ║")
        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  Reasoning: {result.reasoning:<43}║")
        print(f"╚══════════════════════════════════════════════════════════════╝")

        # Read additional context (if available)
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                current_step = state.get("current_step")
                current_module = state.get("current_module")
                if current_step or current_module:
                    print(f"\n📊 State Context: Step {current_step}, Module {current_module}")
            except Exception:
                pass

        return 0

    def cmd_update_step(self, args):
        """Update Phase/Step/Module tracking

        Usage:
            python cli.py update-step --step 3.1 --module LexiconMapper --action "Start implementation"
        """
        from quality_gate.unified_gate import UnifiedGate
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        action = args.action or None

        ug = UnifiedGate(project_path=repo_path)

        # Gap 5: time tracking init - reset time when Phase changes
        state_file = repo_path / ".methodology" / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                current_phase = state.get("current_phase")
                # Prefer --phase parameter, otherwise parse from step prefix (e.g. "3.1" -> phase 3)
                new_phase = getattr(args, 'phase', None)
                if new_phase is None:
                    step_str = getattr(args, 'step', '') or ''
                    m = re.match(r'^(\d+)', step_str)
                    if m:
                        new_phase = int(m.group(1))

                if new_phase is not None and current_phase != new_phase:
                    now_iso = datetime.now().isoformat()
                    state["start_time"] = now_iso
                    state["hr13_triggered"] = False
                    state["hr13_remaining_minutes"] = None
                    state["last_checkpoint"] = now_iso
                    # Estimated duration can be overridden by --estimated-minutes
                    est = getattr(args, 'estimated_minutes', None)
                    state["estimated_minutes"] = est if est else state.get("estimated_minutes", 180)
                    if "checkpoint_interval_minutes" not in state:
                        state["checkpoint_interval_minutes"] = 60
                    state["current_phase"] = new_phase
                    state_file.write_text(json.dumps(state, indent=2))
            except Exception:
                pass  # do not block main flow

        ug.update_step(
            step=args.step,
            module=args.module,
            next_action=action
        )
        print(f"✅ Updated: Step={args.step}, Module={args.module}, Action={action}")
        return 0

    def cmd_end_phase(self, args):
        """End current Phase

        Usage:
            python cli.py end-phase --phase 3
        """
        from quality_gate.unified_gate import UnifiedGate
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        phase = getattr(args, 'phase', None)
        ug = UnifiedGate(project_path=repo_path)
        ug.end_phase(phase=int(phase) if phase else None)
        print(f"✅ Phase ended")
        return 0

    def cmd_update_artifact(self, args):
        """Update artifact version in state.json

        Usage:
            python cli.py update-artifact --name SRS.md --version v1.0.0 --path SRS.md --summary "Complete requirements specification"
        """
        from quality_gate.unified_gate import UnifiedGate
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        ug = UnifiedGate(project_path=repo_path)
        ug.update_artifact(
            name=args.name,
            version=args.version,
            path=args.path,
            summary=args.summary or ""
        )
        print(f"✅ Artifact updated: {args.name}@{args.version}")
        return 0

    def cmd_add_task(self, args):
        """Add task to task graph

        Usage:
            python cli.py add-task --task-id task-001 --title "Complete login feature" --dependencies ""
        """
        from quality_gate.unified_gate import UnifiedGate
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        ug = UnifiedGate(project_path=repo_path)
        ug.add_task(
            task_id=args.task_id,
            title=args.title,
            dependencies=args.dependencies.split(",") if args.dependencies else []
        )
        print(f"✅ Task added: {args.task_id}")
        return 0

    def cmd_task_result(self, args):
        """Update task result

        Usage:
            python cli.py task-result --task-id task-001 --result "Done" --summary "Login feature implemented" --status completed
        """
        from quality_gate.unified_gate import UnifiedGate
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        ug = UnifiedGate(project_path=repo_path)
        ug.update_task_result(
            task_id=args.task_id,
            result=args.result,
            summary=args.summary,
            status=args.status or "completed"
        )
        print(f"✅ Task result updated: {args.task_id}")
        return 0

    def cmd_verify_artifact(self, args):
        """Verify_Agent - independently verify artifact correctness"""
        from quality_gate.unified_gate import UnifiedGate
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        phase = getattr(args, 'phase', 3)

        ug = UnifiedGate(project_path=repo_path)
        state = ug._read_state()

        # Read Phase artifacts
        artifacts = state.get("artifacts", {})

        # Simple verification: check key artifacts exist and have versions
        issues = []
        for name, info in artifacts.items():
            if not info.get("version"):
                issues.append(f"{name}: missing version number")
            if not info.get("commit_hash"):
                issues.append(f"{name}: missing commit hash")

        if issues:
            print("⚠️ Verification Issues:")
            for issue in issues:
                print(f"  - {issue}")
            print("Verdict: REJECTED")
        else:
            print("✅ All artifacts verified")
            print("Verdict: APPROVED")

        return 0

    def cmd_retry_test(self, args):
        """Test retry mechanism"""
        import time
        from quality_gate.fault_tolerance import RetryHandler, DynamicPromptAdjuster

        attempt_count = [0]

        def unreliable_task():
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise Exception(f"Simulated failure #{attempt_count[0]}")
            return f"Success on attempt {attempt_count[0]}"

        handler = RetryHandler(max_retries=3, base_delay=1.0)

        print("Testing RetryHandler with dynamic prompt adjustment...")
        print(f"Constraint for retry 1: {DynamicPromptAdjuster.get_constraint(1)}")
        print(f"Constraint for retry 2: {DynamicPromptAdjuster.get_constraint(2)}")
        print(f"Constraint for retry 3: {DynamicPromptAdjuster.get_constraint(3)}")
        print()

        def on_retry(count, error):
            constraint = DynamicPromptAdjuster.get_constraint(count)
            print(f"  Retry {count}: {error}")
            print(f"  Constraint injected: {constraint}")

        try:
            result = handler.execute(unreliable_task, on_retry=on_retry)
            print(f"Result: {result}")
        except Exception as e:
            print(f"Failed after retries: {e}")

        return 0

    # ============================================================================
    # FSM State Machine Commands
    # ============================================================================

    def cmd_fsm_status(self, args):
        """FSM state query"""
        from quality_gate.unified_gate import FSMStateMachine
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        fsm = FSMStateMachine(project_path=repo_path)
        state = fsm.ug._read_state()

        current = state.get('status', 'INIT')
        state_history = state.get('state_history', [])

        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  FSM State Machine Status                                     ║
╠══════════════════════════════════════════════════════════════╣
║  Current State: {current:<47}║""")

        if state_history:
            print("╠══════════════════════════════════════════════════════════════╣")
            print("║  State History (last 5):                                     ║")
            for entry in state_history[-5:]:
                ts = entry.get('timestamp', '')[:19]
                print(f"║    {entry['from']} → {entry['to']:<10} ({ts})                        ║")
                if entry.get('reason'):
                    reason = entry['reason'][:40]
                    print(f"║      Reason: {reason:<42}║")
        else:
            print("╠══════════════════════════════════════════════════════════════╣")
            print("║  No state transitions recorded yet.                          ║")

        print("╚══════════════════════════════════════════════════════════════╝")
        return 0

    def cmd_fsm_transition(self, args):
        """FSM manual state transition"""
        from quality_gate.unified_gate import FSMStateMachine, ProjectState
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        fsm = FSMStateMachine(project_path=repo_path)
        state = fsm.ug._read_state()

        target = getattr(args, 'to', None)
        if not target:
            print("❌ --to <state> is required")
            print("    Valid states: INIT, RUNNING, VERIFYING, WRITING, PAUSED, FREEZE, COMPLETED")
            return 1

        try:
            to_state = ProjectState[target.upper()]
        except KeyError:
            print(f"❌ Invalid state: {target}")
            print("    Valid states: INIT, RUNNING, VERIFYING, WRITING, PAUSED, FREEZE, COMPLETED")
            return 1

        current = state.get('status', 'INIT')
        try:
            from_state = ProjectState(current)
        except ValueError:
            from_state = ProjectState.INIT

        reason = args.reason or "Manual transition"
        success = fsm.transition(from_state, to_state, reason)

        if success:
            print(f"✅ Transition: {from_state.value} → {to_state.value}")
            print(f"   Reason: {reason}")
        else:
            print(f"❌ Transition failed: {from_state.value} → {to_state.value}")
            print(f"   Current state is {current}, not {from_state.value}")
            print(f"   Or transition is not allowed by FSM rules")
            return 1

        return 0

    def cmd_fsm_resume(self, args):
        """FSM release brake (PAUSED -> RUNNING)"""
        from quality_gate.unified_gate import FSMStateMachine, ProjectState
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        fsm = FSMStateMachine(project_path=repo_path)

        current = fsm.get_state()
        if current != ProjectState.PAUSED:
            print(f"⚠️  Current state is {current.value}, not PAUSED")
            print("    Use 'fsm-transition --to RUNNING' to manually transition")
            return 1

        success = fsm.resume()
        if success:
            print("✅ Brake released: PAUSED → RUNNING")
        else:
            print("❌ Failed to release brake")
            return 1

        return 0

    def cmd_fsm_unfreeze(self, args):
        """FSM unfreeze (FREEZE -> INIT)"""
        from quality_gate.unified_gate import FSMStateMachine, ProjectState
        from pathlib import Path

        repo_path = Path(args.repo or Path.cwd())
        fsm = FSMStateMachine(project_path=repo_path)

        current = fsm.get_state()
        if current != ProjectState.FREEZE:
            print(f"⚠️  Current state is {current.value}, not FREEZE")
            print("    Use 'fsm-transition --to INIT' to manually transition")
            return 1

        success = fsm.unfreeze()
        if success:
            print("✅ Unfreezed: FREEZE → INIT")
            print("   Project has been audited and is ready to restart")
        else:
            print("❌ Failed to unfreeze")
            return 1

        return 0

    # ============================================================================
    # ============================================================================
    # auto-research: AutoResearch Quality Improvement
    # ============================================================================

    def cmd_auto_research(self, args):
        """
        Execute AutoResearch quality improvement

        This command is now a thin wrapper that spawns a sub-agent
        to do the actual work. The sub-agent has sessions_spawn access
        and can perform real code modifications.
        """
        from pathlib import Path
        import sys

        project_path = Path(args.project).resolve()
        phase = args.phase
        iterations = args.iterations
        timeout = getattr(args, 'timeout', 1800)  # default 30 min

        # Check project-config.yaml
        config_file = project_path / "project-config.yaml"
        auto_research_enabled = True  # default

        if config_file.exists():
            import yaml
            try:
                with open(config_file) as f:
                    config = yaml.safe_load(f)
                    auto_research_enabled = config.get('auto_research', {}).get('enabled', True)
            except Exception as e:
                print(f"⚠️  Could not read project-config.yaml: {e}")

        if not auto_research_enabled:
            print(f"❌ AutoResearch is disabled in project-config.yaml")
            print(f"   Set auto_research.enabled: true to enable")
            return 1

        # Get phase dimensions
        phase_dimensions = {
            3: ['D1', 'D5', 'D6', 'D7'],
            4: ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7'],
            5: ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9'],
        }
        dimensions = phase_dimensions.get(phase, phase_dimensions[3])

        print(f"\n{'='*60}")
        print(f"🔬 AutoResearch Quality Improvement (Sub-agent Mode)")
        print(f"{'='*60}")
        print(f"   Project: {project_path}")
        print(f"   Phase: {phase}")
        print(f"   Active dimensions: {', '.join(dimensions)}")
        print(f"   Max iterations: {iterations}")
        print(f"   Enabled: {auto_research_enabled}")
        print()
        print(f"⚠️  This command spawns a sub-agent to perform AutoResearch.")
        print(f"   The sub-agent has full sessions_spawn access.")
        print()
        print(f"To run manually, spawn a sub-agent with this task:")
        print(f"\n{'='*60}\n")

        # Build the task prompt
        framework_dir = Path(__file__).parent.resolve()
        task = f"""Run AutoResearch for Phase {phase} quality improvement.

Project: {project_path}
Framework: {framework_dir}

## Task
1. Run quality assessment:
   cd {project_path}
   python3 {framework_dir}/quality_dashboard/dashboard.py assess --project . --phase {phase}

2. Fix ALL dimensions below 85% (not just below 70):
   - D1-D9 as appropriate for Phase {phase}
   - Use sessions_spawn to spawn developer/reviewer sub-agents
   - Fix real issues: complexity refactoring, type fixes, security, etc.

3. Report final scores for all dimensions.

Target: ≥85% on ALL active dimensions."""

        print(task)
        print(f"{'='*60}\n")
        print(f"Recommended: Ask your main agent to spawn this task.")
        
        return 0
    # run-phase: Single Entry Point for Phase Execution
    # ============================================================================

    def cmd_run_phase(self, args):
        """
        Single Entry Point for Phase Execution

        This command:
        - ✅ calls SubagentIsolator.spawn(), does not directly use sessions_spawn
        - ✅ reads state.json and obeys FSM state
        - ✅ stops at Pre-flight on failure, does not proceed to execution
        - ✅ automatically runs Constitution check in Post-flight
        - ✅ auto-obeys on HR-12/13/14 trigger (pause/freeze)
        - ❌ does not bypass any HR rules
        - ❌ does not skip Phase order
        """
        import sys
        import json
        import time
        from pathlib import Path
        from datetime import datetime

        phase = args.phase
        phase_type = getattr(args, 'type', None)  # Get from args.type if available
        repo_path = Path(args.repo or Path.cwd())

        # === PRE-FLIGHT CHECKS ===
        print(f"\n{'='*60}")
        print(f"🚀 run-phase --phase {phase}")
        print(f"{'='*60}\n")

        # 1. FSM State Validation
        print(f"[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: FSM State Check")
        from quality_gate.unified_gate import FSMStateMachine, ProjectState
        fsm = FSMStateMachine(project_path=repo_path)
        current_state = fsm.get_state()

        if current_state == ProjectState.FREEZE:
            print(f"❌ Project is FREEZE. Run 'fsm-unfreeze' first.")
            return 1
        if current_state == ProjectState.PAUSED:
            print(f"⚠️  Project is PAUSED. Use 'fsm-resume' or 'fsm-transition --to RUNNING' to continue.")
            return 1

        # 2. Phase Sequence Validation
        state_file = repo_path / ".methodology" / "state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            current_phase = state.get("current_phase", 0)
            if current_phase >= phase and current_state == ProjectState.INIT:
                print(f"⚠️  Phase {phase} has already been completed or is in progress.")
                print(f"    Current phase: {current_phase}, target: {phase}")
                print(f"    Use --step to continue from a specific step.")

        # 3. Phase-Aware Constitution Check (Pre-flight)
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: Constitution Check (Phase {phase})")
        
        from quality_gate.phase_aware_constitution import run_phase_constitution
        from pathlib import Path
        
        # Pre-flight: only check Phase (N-1) prerequisite outputs
        result = run_phase_constitution(phase, "preflight", Path(repo_path))

        if not result.passed:
            print(f"❌ Phase {phase} Pre-flight Check FAILED")
            print(f"   Missing prerequisites:")
            for m in result.missing:
                print(f"     - {m}")
            print(f"   Score: {result.score:.0f}%")
            print(f"\n💡 Complete Phase {phase - 1} before starting Phase {phase}")
            return 1
        print(f"✅ Phase {phase} Pre-flight Check PASSED")
        print(f"   Ready artifacts: {len(result.ready)}")
        print(f"   Score: {result.score:.0f}%")

        # 4. Tool Registry Check
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: Tool Registry Check")
        from tool_registry import ToolRegistry
        tools = ToolRegistry.list_tools()
        if not tools:
            print(f"⚠️  No tools registered. Run 'python cli.py tool-registry --list'")
        else:
            print(f"✅ {len(tools)} tools registered: {', '.join(tools[:5])}{'...' if len(tools) > 5 else ''}")

        # 5. Pre-flight Session Save
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: Session Save")
        from checkpoint_manager import SessionManager
        sm = SessionManager()
        session_id = f"pre-phase-{phase}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        preflight_state = {
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
            "constitution_score": result.score,
            "tools_count": len(tools),
            "fsm_state": current_state.value if hasattr(current_state, 'value') else str(current_state)
        }
        save_path = sm.save(session_id, preflight_state)
        print(f"✅ Pre-flight session saved: {session_id}")
        print(f"   Path: {save_path}")

        # 6. P0-5: SubagentIsolator Hooks Verification (HR-10 compliant)
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: SubagentIsolator Hooks Verification")
        try:
            from subagent_isolator import SubagentIsolator
            from sessions_spawn_logger import SessionsSpawnLogger
            # Verify SubagentIsolator can be instantiated normally (hooks callable)
            si = SubagentIsolator(project_path=str(repo_path))
            spawn_log_path = repo_path / ".methodology" / "sessions_spawn.log"
            print(f"✅ [PRE-FLIGHT] SubagentIsolator hooks verified")
            print(f"   sessions_spawn.log path: {spawn_log_path}")
            # P2-1: Auto-create sessions_spawn.log
            sessions_spawn_log = repo_path / ".methodology" / "sessions_spawn.log"
            # P2-1: Auto-create sessions_spawn.log with validation
            sessions_spawn_log.parent.mkdir(parents=True, exist_ok=True)
            if not sessions_spawn_log.exists():
                # Use Ralph's schema to create properly formatted log
                import json
                try:
                    from ralph_mode.schema_validator import SessionsSpawnLogValidator
                    validator = SessionsSpawnLogValidator()
                    sessions_spawn_log.write_text(json.dumps(validator.create_empty_log(), indent=2))
                except ImportError:
                    sessions_spawn_log.write_text("{}")
                print(f"✅ [PRE-FLIGHT] Created sessions_spawn.log")
            else:
                # Validate existing log format
                logger = SessionsSpawnLogger(repo_path)
                result = logger.validate()
                if result["valid"]:
                    summary = logger.get_summary()
                    print(f"✅ [PRE-FLIGHT] sessions_spawn.log exists ({summary['total_entries']} entries)")
                    if summary['fr_tasks']:
                        print(f"   FR tasks: {', '.join(summary['fr_tasks'])}")
                else:
                    print(f"⚠️  [PRE-FLIGHT] sessions_spawn.log has {len(result['errors'])} format errors")
                    print(f"   First error: {result['errors'][0]}")
            
            # P0: integrate IterationManager (Aspect 7)
            try:
                from integration_manager import IntegrationManager
                iteration_mgr = IntegrationManager(phase=phase, task_id="phase-init", repo_path=repo_path)
                print(f"✅ [PRE-FLIGHT] IterationManager initialized")
                print(f"   - Tracking HR-12: A/B review > 5 rounds -> PAUSE")
                print(f"   - Tracking HR-13: Phase execution > estimated x3 -> PAUSE")
            except ImportError:
                print(f"⚠️  [PRE-FLIGHT] IntegrationManager not available, using manual tracking")
            
            # P0: integrate ToolDispatcher (Aspect 6)
            try:
                from tool_dispatcher import ToolDispatcher
                tool_dispatcher = ToolDispatcher(repo_path=repo_path)
                print(f"✅ [PRE-FLIGHT] ToolDispatcher ready")
                print(f"   - on_spawn: when Subagent is spawned")
                print(f"   - on_message: when messages >50/100/200")
                print(f"   - on_error: when error occurs")
                print(f"   - on_complete: when task completes")
            except ImportError:
                print(f"⚠️  [PRE-FLIGHT] ToolDispatcher not available")
            
            # P2-2: ToolRegistry integration
            try:
                from tool_registry import ToolRegistry
                tools = ToolRegistry.list_tools()
                print(f"✅ [PRE-FLIGHT] ToolRegistry: {len(tools)} tools registered")
                if len(tools) == 0:
                    print(f"   ⚠️ No tools registered. Use 'python cli.py tool-registry --register'")
            except ImportError:
                print(f"⚠️  [PRE-FLIGHT] ToolRegistry not available")
        except Exception as e:
            print(f"⚠️  SubagentIsolator hook verification failed: {e}")

            rlm = None

        # === EXECUTE ===
        print(f"\n{'='*60}")
        print(f"▶ EXECUTE Phase {phase}")
        print(f"{'='*60}\n")

        # Load P{N}_SOP.md from Framework (not Project)
        # SOPs are Framework artifacts, not Project artifacts
        METHODOLOGY_DIR = Path(__file__).parent.resolve()
        sop_path = METHODOLOGY_DIR / "docs" / f"P{phase}_SOP.md"
        if not sop_path.exists():
            print(f"❌ SOP not found: {sop_path}")
            return 1

        print(f"📋 Loading SOP: docs/P{phase}_SOP.md")
        sop_content = sop_path.read_text()
        # === RALPH MODE LIFECYCLE (v1.1) ===
        # Ralph Mode provides background monitoring for Phase execution
        _ralph_manager = None
        try:
            from ralph_mode import RalphLifecycleManager
            from ralph_mode.schema_validator import SessionsSpawnLogValidator
            
            rlm = RalphLifecycleManager(repo_path)
            
            # Check if Ralph is already running for this phase (MVP: default to reuse)
            existing = rlm.get_running_ralph(phase)
            if existing:
                print(f"⚠️  Phase {phase} Ralph already running: {existing.task_id}")
                print(f"   Defaulting to continue monitoring (MVP reuse behavior)")
                rlm._current_task_id = existing.task_id
                rlm._current_state = existing
            else:
                # Parse FR list from SOP for estimated duration
                fr_patterns = []
                import re
                fr_matches = re.findall(r'\b(FR-\d+)\b', sop_content)
                seen = set()
                for fr in fr_matches:
                    if fr not in seen:
                        fr_patterns.append(fr)
                        seen.add(fr)
                
                # Estimate duration: 10 minutes per FR
                estimated_minutes = max(30, len(fr_patterns) * 10)
                
                # Start Ralph
                task_id = rlm.start(phase=phase, estimated_minutes=estimated_minutes)
                print(f"✅ [RALPH] Started: {task_id}")
                print(f"   Estimated duration: {estimated_minutes} minutes")
                print(f"   HR-13 threshold: {estimated_minutes * 3} minutes")
            
            _ralph_manager = rlm
        except ImportError as e:
            print(f"⚠️  [RALPH] Ralph Mode not available: {e}")
            rlm = None
        except Exception as e:
            print(f"⚠️  [RALPH] Failed to start: {e}")
        print(f"✅ SOP loaded ({len(sop_content)} chars)\n")

        # Execute steps from SOP
        task_desc = args.task or f"Phase {phase} execution"
        print(f"📌 Task: {task_desc}")

        # Log to run-phase.log
        log_path = repo_path / ".methodology" / "run-phase.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def write_log(event: str, message: str, duration_ms: int = None, subagent_session: str = None):
            entry = {
                "timestamp": datetime.now().isoformat(),
                "phase": phase,
                "event": event,
                "message": message,
                "duration_ms": duration_ms,
                "subagent_session": subagent_session
            }
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

        write_log("EXECUTE_START", f"Phase {phase} started - {task_desc}")

        # === v6.88: FR Execution Loop (A/B Review Pattern) ===
        #
        # Flow: Johnny → Developer → Reviewer → [REJECT → Developer] × N → APPROVE → Next FR
        # HR-12: Max 5 review iterations before PAUSE
        # HR-13: Phase timeout monitoring
        
        step = args.step or "1"
        
        # --- Import SubagentIsolator ---
        try:
            from subagent_isolator import SubagentIsolator, AgentRole
            from sessions_spawn_logger import SessionsSpawnLogger
            SI_AVAILABLE = True
        except ImportError as e:
            print(f"⚠️  SubagentIsolator not available: {e}")
            print(f"   Falling back to skeleton mode")
            SI_AVAILABLE = False
        
        # --- Parse FR list from SOP ---
        fr_patterns = []
        import re
        # Match patterns like "FR-01", "FR-02", etc in SOP
        fr_matches = re.findall(r'\b(FR-\d+)\b', sop_content)
        # Deduplicate while preserving order
        seen = set()
        for fr in fr_matches:
            if fr not in seen:
                fr_patterns.append(fr)
                seen.add(fr)
        
        if not fr_patterns:
            # No FRs found, check for step patterns like "Step 3.1", "Step 3.2"
            step_matches = re.findall(r'Step\s+(\d+\.\d+)', sop_content)
            if step_matches:
                fr_patterns = [f"Step {s}" for s in step_matches[:5]]  # Limit to 5 steps
        
        print(f"\n📍 Executing Phase {phase} FRs: {fr_patterns or ['(no FRs found, using skeleton)']}")
        print(f"   Step: {step}")
        
        # v6.102 FIX: Check --resume flag to skip PRE-FLIGHT
        if hasattr(args, 'resume') and args.resume:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] RESUME: Skipping PRE-FLIGHT")
            print(f"   (Use without --resume to run PRE-FLIGHT checks)")
        else:
            # === PRE-FLIGHT CHECKS ===
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: Starting checks...")
            
            # 1. FSM State Validation
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PRE-FLIGHT: FSM State Check")
        #
        # IMPORTANT: sessions_spawn is a runtime tool, NOT a Python module.
        # cli.py CANNOT call sessions_spawn directly.
        # Agent MUST execute FRs directly using sessions_spawn.
        #
        # Agent workflow:
        # 1. Read plan from Plan Phase output
        # 2. Execute FRs using sessions_spawn directly
        # 3. Call PhaseHooks for monitoring (optional but recommended)
        # 4. Run POST-FLIGHT: python cli.py run-phase --phase {phase} --resume
        
        print(f"\n{'='*60}")
        print(f"📋 EXECUTION GUIDE FOR AGENT (v6.102)")
        print(f"{'='*60}")
        print(f"""
Phase {phase} Ready for Agent Execution

IMPORTANT: This CLI cannot call sessions_spawn directly.
Agent MUST execute FRs using sessions_spawn tool.

Agent Workflow:
1. Read SRS.md and SAD.md
2. For each FR:
   a. Developer: sessions_spawn(task=dev_task) → returns JSON with files
   b. Parse JSON, write files to disk
   c. Reviewer: sessions_spawn(task=rev_task) → returns APPROVE/REJECT
   d. Record results in PhaseHooks (optional)
3. Run POST-FLIGHT: python cli.py run-phase --phase {phase} --resume

Full execution script is in templates/plan_phase_template.md Section 17.
""")
        
        # NOTE (v6.109): If NOT resuming, just show guide and exit.
        # Agent must execute FRs, then call run-phase --resume for POST-FLIGHT.
        if not (hasattr(args, 'resume') and args.resume):
            print(f"\n✅ PRE-FLIGHT passed. Agent execution guide shown above.")
            print(f"\n   After Agent executes FRs, run:")
            print(f"   python cli.py run-phase --phase {phase} --resume")
            return 0
        
        # Placeholder values for POST-FLIGHT (only when --resume)
        fr_results = []
        approved = 0
        total = len(fr_patterns) if fr_patterns else 0

        write_log("EXECUTE_FR_COMPLETE", f"Phase {phase}: Agent execution - see plan_phase_template.md Section 17")
        write_log("EXECUTE_FR_COMPLETE", f"Phase {phase}: {approved}/{total} FRs approved")

        # === POST-FLIGHT ===
        # Default phase_type if not set
        if not phase_type:
            check_type = "QUALITY_REPORT"  # Default for Phase 6
        
        print(f"\n{'='*60}")
        print(f"✓ POST-FLIGHT: Final Constitution Check")
        print(f"{'='*60}\n")

        # === v6.86 fix: use run_constitution_check directly (runner was never defined) ===
        result_final = run_constitution_check_with_feedback(check_type, docs_path=str(repo_path / "docs"), current_phase=phase)
        if result_final.score < 80:
            print(f"⚠️  Final Constitution score {result_final.score:.0f}% < 80%")
            print(f"   Violations: {result_final.violations}")
        else:
            print(f"✅ Final Constitution score: {result_final.score:.0f}%")

        # === AutoResearch Quality Check (v7.35) ===
        if not getattr(args, 'no_autoresearch', False):
            # Check project-config.yaml for auto_research.enabled
            config_file = repo_path / "project-config.yaml"
            auto_research_enabled = True  # default
            if config_file.exists():
                try:
                    import yaml
                    with open(config_file) as f:
                        config = yaml.safe_load(f)
                        auto_research_enabled = config.get('auto_research', {}).get('enabled', True)
                except Exception:
                    pass
            
            if auto_research_enabled:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] POST-FLIGHT: AutoResearch Quality Check")
                print(f"   Enabled: yes (project-config.yaml)")
                
                # Get phase dimensions
                phase_dimensions = {
                    3: ['D1', 'D5', 'D6', 'D7'],
                    4: ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7'],
                    5: ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9'],
                }
                dimensions = phase_dimensions.get(phase, phase_dimensions[3])
                print(f"   Active dimensions: {', '.join(dimensions)}")
                print(f"   Timeout: {getattr(args, 'timeout', 1800)}s")
                
                # Add quality_dashboard to path and run
                dashboard_path = Path(__file__).parent / "quality_dashboard"
                if str(dashboard_path) not in sys.path:
                    sys.path.insert(0, str(dashboard_path))
                
                try:
                    from agent_auto_research import AgentDrivenAutoResearch
                    ar_agent = AgentDrivenAutoResearch(str(repo_path), phase=phase)
                    ar_result = ar_agent.run(max_iterations=3)
                    print(f"   AutoResearch: {ar_result.get('status', 'completed')}")
                except Exception as e:
                    print(f"   ⚠️  AutoResearch failed: {e}")
            else:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] POST-FLIGHT: AutoResearch")
                print(f"   Enabled: no (project-config.yaml)")
        else:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] POST-FLIGHT: AutoResearch")
            print(f"   Skipped: --no-autoresearch flag")

        # === Generate STAGE_PASS.md (v7.7 fix) ===
        if approved >= total and result_final.score >= 80:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] POST-FLIGHT: Generating STAGE_PASS.md")
            try:
                from quality_gate.stage_pass_generator import STAGEPassGenerator
                generator = STAGEPassGenerator(repo_path=str(repo_path), phase=phase)
                output_path = generator.generate()
                print(f"✅ STAGE_PASS.md generated: {output_path}")
            except Exception as e:
                print(f"⚠️ STAGE_PASS generation failed: {e}")

        # Update state.json (v6.94 fix: only on success)
        # v6.94: Only update state.json if phase succeeded
        if approved >= total and result_final.score >= 80:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] POST-FLIGHT: Update state.json (SUCCESS)")
            if state_file.exists():
                state = json.loads(state_file.read_text())
                state["current_phase"] = phase
                state["last_update"] = datetime.now().isoformat()
                state_file.write_text(json.dumps(state, indent=2))
                print(f"✅ state.json updated: current_phase = {phase}")
        else:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] POST-FLIGHT: NOT updating state.json (phase failed)")
            print(f"   approved: {approved}/{total}, constitution: {result_final.score:.0f}%")

        write_log("EXECUTE_COMPLETE", f"Phase {phase} completed - score: {result_final.score:.0f}%")

        # === RALPH MODE STOP (v1.1) ===
        # Stop Ralph based on execution result
        if _ralph_manager is not None:
            try:
                if approved >= total and result_final.score >= 80:
                    # M1: All FR completed + Constitution passed → STOP + SUCCESS
                    _ralph_manager.stop(reason="completed")
                    print(f"✅ [RALPH] Stopped: phase completed successfully")
                else:
                    # Constitution failed → Ralph continues monitoring (user decides)
                    print(f"⚠️  [RALPH] Constitution failed - Ralph continues monitoring")
                    print(f"   User can manually stop with: ralph stop {_ralph_manager._current_task_id}")
            except Exception as e:
                print(f"⚠️  [RALPH] Failed to stop: {e}")

        # Summary
        success = (approved >= total and result_final.score >= 80)
        print(f"\n{'='*60}")
        print(f"📊 Phase {phase} Execution Summary")
        print(f"{'='*60}")
        print(f"   FRs Approved: {approved}/{total}")
        print(f"   Constitution Score: {result_final.score:.0f}%")
        print(f"   Session ID: {session_id}")
        print(f"   Log: {log_path}")
        if success:
            print(f"\n✅ Phase {phase} completed successfully")
            return 0
        else:
            print(f"\n❌ Phase {phase} completed with failures")
            return 1  # v6.94: Return error code on failure

    # ============================================================================
    # plan-phase Helper Functions
    # ============================================================================

    def _parse_srs_fr_list(self, repo_path: Path) -> list:
        """Parse SRS.md, extract FR list"""
        srs_paths = [
            repo_path / "SRS.md",
            repo_path / "01-requirements" / "SRS.md",
        ]
        srs_path = None
        for p in srs_paths:
            if p.exists():
                srs_path = p
                break
        
        if not srs_path:
            return []
        
        content = srs_path.read_text()
        import re
        # Match FR-01, FR-02 etc. (supports fullwidth colon and halfwidth :)
        fr_pattern = re.compile(r'(FR-\d+)[:][^\n]+', re.IGNORECASE)
        frs = []
        for m in fr_pattern.finditer(content):
            fr_id = m.group(1).upper()
            raw = m.group(0)
            # Remove FR-XX: prefix to get title
            if ':' in raw:
                title = raw.split(':', 1)[1].strip()
            elif ':' in raw:
                title = raw.split(':', 1)[1].strip()
            else:
                title = raw.strip()
            title = title[:60]
            frs.append({
                "fr": fr_id,
                "title": title,
                "srs_path": str(srs_path)
            })
        
        # If regex fails, use simple keyword search
        if not frs:
            fr_lines = re.findall(r'(FR-\d+)', content, re.IGNORECASE)
            seen = set()
            for fr_id in fr_lines:
                if fr_id.upper() not in seen:
                    seen.add(fr_id.upper())
                    frs.append({"fr": fr_id.upper(), "title": fr_id, "srs_path": str(srs_path)})
        
        # Deduplicate by FR ID
        seen_frs = {}
        deduped = []
        for fr in frs:
            if fr["fr"] not in seen_frs:
                seen_frs[fr["fr"]] = fr
                deduped.append(fr)
        frs = deduped
        
        return frs

    def _parse_sad_modules(self, repo_path: Path) -> list:
        """Parse SAD.md, extract FR -> module file mapping (enhanced)"""
        sad_paths = [
            repo_path / "02-architecture" / "SAD.md",
            repo_path / "SAD.md",
            repo_path / "templates" / "SAD.md",
        ]
        sad_path = None
        for p in sad_paths:
            if p.exists():
                sad_path = p
                break
        
        if not sad_path:
            return []
        
        content = sad_path.read_text()
        import re
        
        # Priority: parse Markdown table format (FR ID | requirement | app/...)
        # e.g.: | **FR-01** | TaiwanLexicon >= 50 term mappings | `app/processing/lexicon_mapper.py` |
        # supports both 'app/' and '03-development/src/' paths (backward compatible)
        simple_pattern = re.compile(r'FR-(\d+)[^\n]*?`?(?:app/|03-development/src/)([^\s`]+)`?', re.DOTALL)
        modules = []
        seen_frs = set()
        for m in simple_pattern.finditer(content):
            fr_num = m.group(1)
            if fr_num in seen_frs:
                continue
            file_path = m.group(2) or ""
            seen_frs.add(fr_num)
            # Infer module name from path: app/processing/lexicon_mapper.py -> lexicon_mapper
            if '/' in file_path:
                filename = file_path.split('/')[-1].replace('.py', '')
                module_name = filename
                # Normalize: prefer app/ prefix (project standard)
                if file_path.startswith('03-development/src/'):
                    file_path = file_path.replace('03-development/src/', 'app/')
                elif not file_path.startswith('app/'):
                    file_path = f"app/{file_path}"
            else:
                module_name = f"Module{fr_num}"
            modules.append({
                "fr": f"FR-{fr_num}",
                "module": module_name,
                "file": file_path,
                "source": "table"
            })
        
        if modules:
            return modules
        
        # Fallback: old pattern matching
        module_pattern = re.compile(
            r'(##?\s*Module\s*(\d+)|###?\s*(\d+)[^\n]*Module[^\n]*|FR-(\d+)[^\n]*module)',
            re.IGNORECASE
        )
        for m in module_pattern.finditer(content):
            module_num = m.group(2) or m.group(3) or "?"
            fr_ref = m.group(4) or ""
            modules.append({
                "module": f"Module {module_num}",
                "fr": f"FR-{fr_ref}" if fr_ref else "",
                "sad_path": str(sad_path)
            })
        return modules


    def _generate_fr_table(self, frs: list, modules: list) -> str:
        """Generate FR-by-FR task table (enhanced: using SAD real mapping)"""
        if not frs:
            return "*(FR list not found)*"
        
        table = "| FR | Module | Output File | Test File | Verify Command |\n|------|------|---------|----------|---------|\n"
        for fr in frs:
            fr_num = fr["fr"].lower().replace("-", "").replace("fr", "")
            # Find matching FR from modules (case-insensitive)
            mod_match = next((m for m in modules if m.get("fr", "").upper() == fr["fr"].upper()), None)
            if mod_match:
                module = mod_match.get("module", "—")
                impl_path = mod_match.get("file", "")
            else:
                module = "—"
                impl_path = ""
            
            # Infer test file (from impl_path)
            # Support both 'app/' and '03-development/src/' paths
            if impl_path and ("app/" in impl_path or "03-development/src/" in impl_path):
                filename = impl_path.split("/")[-1].replace(".py", "")
                test_path = f"tests/test_fr{fr_num.zfill(2)}_{filename}.py"
            else:
                test_path = f"tests/test_fr{fr_num.zfill(2)}.py"
            
            verify_cmd = f"`pytest tests/test_fr{fr_num.zfill(2)}*.py -v`"
            table += f"| {fr['fr']} | {module} | `{impl_path}` | `{test_path}` | {verify_cmd} |\n"
        return table

    def _generate_deliverable_structure(self, frs: list, modules: list) -> str:
        """Generate output tree from FR/Module mapping"""
        if not modules:
            return "*(please parse from SAD.md §1.3)*"
        
        # Group by directory (normalize paths)
        dirs = {}
        for m in modules:
            file_path = m.get('file', '')
            if '/' in file_path:
                # Normalize: strip app/ or 03-development/src/ prefix for grouping
                if file_path.startswith('03-development/src/'):
                    rel_path = file_path.replace('03-development/src/', '')
                elif file_path.startswith('app/'):
                    rel_path = file_path.replace('app/', '')
                else:
                    rel_path = file_path
                dir_name = '/'.join(rel_path.split('/')[:-1])
                filename = rel_path.split('/')[-1]
                if dir_name not in dirs:
                    dirs[dir_name] = []
                dirs[dir_name].append(filename)
        
        lines = ["app/"]
        for dir_name, files in sorted(dirs.items()):
            lines.append(f"├── {dir_name}/")
            for f in sorted(files):
                lines.append(f"│   ├── {f}")
        
        lines.append("tests/")
        for fr in frs[:5]:
            fr_num = fr['fr'].lower().replace('-', '').replace('fr', '').zfill(2)
            lines.append(f"├── test_fr{fr_num}_*.py")
        if len(frs) > 5:
            lines.append(f"... ({len(frs) - 5} more)")
        
        return '\n'.join(lines)

    def _build_deliverable_checklist(self, phase: int, frs: list, modules: list) -> str:
        """Generate artifact checklist for given Phase (Phase-specific artifacts)."""
        lines = [f"## Phase {phase} Artifacts\n"]

        if phase == 1:
            lines.append("### Specification Outputs")
            lines.append("- [ ] `SRS.md` - Software Requirements Specification")
            lines.append("- [ ] `SPEC_TRACKING.md` - Specification Tracking Matrix")
            lines.append("- [ ] `TRACEABILITY_MATRIX.md` - Requirements Traceability Matrix")
            lines.append("\n### Document Outputs")
            lines.append("- [ ] `AB_COLLABORATION.md` - Developer+Reviewer collaboration log")
            lines.append("- [ ] `sessions_spawn.log` - A/B session complete log")

        elif phase == 2:
            lines.append("### Architecture Outputs")
            lines.append("- [ ] `SAD.md` - Software Architecture Document")
            lines.append("- [ ] `ADR.md` - Architecture Decision Records")
            lines.append("\n### Document Outputs")
            lines.append("- [ ] `AB_COLLABORATION.md` - Developer+Reviewer collaboration log")
            lines.append("- [ ] `sessions_spawn.log` - A/B session complete log")

        elif phase == 3:
            lines.append("### Code Outputs")
            for m in modules:
                mod_name = m.get('module', str(m))
                lines.append(f"- [ ] `{mod_name}` - Module implementation")
            lines.append("\n### Test Outputs")
            for fr in frs[:5]:
                fr_num = fr['fr'].lower().replace('-', '').replace('fr', '')
                lines.append(f"- [ ] `tests/test_fr{fr_num}*.py` - FR-{fr_num} tests")
            if len(frs) > 5:
                lines.append(f"- [ ] ...(total {len(frs)} FRs)")
            lines.append("\n### Verification Outputs")
            lines.append("- [ ] pytest all tests PASS")
            lines.append("- [ ] coverage ≥80%")
            lines.append("- [ ] Phase Truth >90%")
            lines.append("\n### Git Outputs")
            lines.append("- [ ] git push done")

        elif phase == 4:
            lines.append("### Test Outputs")
            lines.append("- [ ] `docs/TEST_PLAN.md` - Test Plan")
            lines.append("- [ ] `docs/TEST_RESULTS.md` - Test Results")
            lines.append("\n### Traceability Outputs")
            lines.append("- [ ] FR<->test mapping rate >=90%")
            lines.append("- [ ] All failing tests documented and fixed")

        elif phase == 5:
            lines.append("### Delivery Outputs")
            lines.append("- [ ] `05-verify/BASELINE.md` - Performance Baseline")
            lines.append("- [ ] `06-quality/MONITORING_PLAN.md` - Monitoring Plan")
            lines.append("\n### Document Outputs")
            lines.append("- [ ] Monitoring thresholds set")
            lines.append("- [ ] Emergency contingency plan defined")

        elif phase == 7:
            lines.append("### Risk Outputs")
            lines.append("- [ ] `07-risk/RISK_REGISTER.md` - Risk Register")
            lines.append("- [ ] `07-risk/RISK_MITIGATION_PLANS.md` - Mitigation Plans")
            lines.append("- [ ] `07-risk/RISK_STATUS_REPORT.md` - Status Report")
            lines.append("\n### Decision Outputs")
            lines.append("- [ ] Decision Gate = 100% (all High/Critical handled)")

        elif phase == 8:
            lines.append("### Configuration Outputs")
            lines.append("- [ ] `08-config/CONFIG_RECORDS.md` - Configuration Records")
            lines.append("- [ ] `requirements.txt` or `pyproject.toml` - dependency lock")
            lines.append("- [ ] `.env.example` - environment variable example")
            lines.append("\n### Release Outputs")
            lines.append("- [ ] Git Tag created and pushed")
            lines.append("- [ ] Version documents updated")

        else:
            lines.append("- [ ] (no standard artifacts for this Phase)")

        return '\n'.join(lines)

    def _generate_thresholds_table(self, phase: int) -> str:
        """
        Generate TH threshold table.
        
        Note: this table is a Quality Gate reference (includes Pre-flight + EXIT gate checks),
        not the strict threshold definitions in SKILL.md Phase EXIT.
        
        SOT EXIT definition: see SKILL.md §4 Phase routing table.
        """
        thresholds = {
            1: [
                ("TH-01", "FSM State", "INIT", "state.json"),
                ("TH-03", "Constitution Correctness", "=100%", "constitution/runner.py --type srs"),
                ("TH-04", "Constitution Safety", "=100%", "constitution/runner.py --type srs"),
                ("TH-08", "AgentEvaluator Standard", ">=80", "phase-verify"),
                ("TH-14", "Specification Completeness", "=100%", "trace-check"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
            ],
            2: [
                ("TH-01", "FSM State", "RUNNING", "state.json"),
                ("TH-03", "Constitution Correctness", "=100%", "constitution/runner.py --type sad"),
                ("TH-04", "Constitution Safety", "=100%", "constitution/runner.py --type sad"),
                ("TH-05", "Constitution Maintainability", ">90%", "constitution/runner.py --type sad"),
                ("TH-08", "AgentEvaluator Standard", ">=80", "phase-verify"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
            ],
            3: [
                ("TH-04", "Constitution Safety", "=100%", "constitution/runner.py --type implementation"),
                ("TH-06", "Constitution Test Coverage", ">90%", "constitution/runner.py --type implementation"),
                ("TH-08", "AgentEvaluator Strict", ">=90", "phase-verify"),
                ("TH-10", "Test Pass Rate", "=100%", "pytest tests/ -v"),
                ("TH-11", "Unit Test Coverage", ">=70%", "pytest --cov=app/ -v"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
                ("TH-16", "Code<->SAD Mapping Rate", "=100%", "trace-check"),
            ],
            4: [
                ("TH-01", "ASPICE Compliance Rate", ">80%", "doc_checker.py"),
                ("TH-03", "Constitution Correctness", "=100%", "constitution/runner.py --type test_plan"),
                ("TH-04", "Constitution Safety", "=100%", "constitution/runner.py --type test_plan"),
                ("TH-05", "Constitution Maintainability", ">90%", "constitution/runner.py --type test_plan"),
                ("TH-06", "Constitution Test Coverage", ">90%", "constitution/runner.py --type test_plan"),
                ("TH-10", "Test Pass Rate", "=100%", "pytest tests/ -v"),
                ("TH-12", "Unit Test Coverage", ">=80%", "pytest --cov=app/ -v"),
                ("TH-13", "SRS FR Coverage", "=100%", "trace-check"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
                ("TH-17", "FR<->Test Mapping Rate", ">=90%", "trace-check"),
            ],
            5: [
                ("TH-02", "Constitution Total Score", ">=80%", "constitution/runner.py --type verification"),
                ("TH-07", "Logic Correctness Score", ">=90", "phase-verify"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
            ],
            6: [
                ("TH-02", "Constitution Total Score", ">=80%", "constitution/runner.py --type quality_report"),
                ("TH-07", "Logic Correctness Score", ">=90", "phase-verify"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
            ],
            7: [
                ("TH-07", "Logic Correctness Score", ">=90", "phase-verify"),
                ("TH-15", "Phase Truth", ">90%", "phase-verify"),
            ],
            8: [
                ("pip freeze", "Dependency Lock", "exists in repo root", "ls requirements*.txt / pip freeze"),
                ("Git Tag", "Version Tag", "exists", "git tag"),
            ],
        }
        
        rows = thresholds.get(phase, thresholds.get(3, []))
        if not rows:
            return "*(no threshold data)*"
        
        lines = ["| TH | Metric | Threshold | Verify Command |", "|------|------|------|---------|"]
        for th_id, name, threshold, cmd in rows:
            lines.append(f"| {th_id} | {name} | {threshold} | `{cmd}` |")
        
        return "\n".join(lines)

    def _generate_external_docs(self, phase: int) -> str:
        """Generate external document list"""
        docs = {
            3: [
                ("SKILL_DOMAIN.md", "Domain knowledge (TTS/SSML/Taiwan Chinese)"),
                ("docs/P3_SOP.md", "Phase 3 detailed steps"),
                ("templates/", "Artifact templates (.md)"),
                ("docs/HYBRID_WORKFLOW_GUIDE.md", "A/B collaboration guidelines"),
                ("docs/CLI_REFERENCE.md", "CLI tool usage"),
                ("docs/ANNOTATION_GUIDE.md", "@FR annotation guidelines"),
                ("docs/VERIFIER_GUIDE.md", "Reviewer guidelines"),
                ("docs/CONSTITUTION_GUIDE.md", "Constitution guidelines"),
            ],
            1: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P1_SOP.md", "Phase 1 detailed steps"),
                ("templates/SRS_TEMPLATE.md", "SRS template"),
            ],
            2: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P2_SOP.md", "Phase 2 detailed steps"),
                ("templates/SAD_TEMPLATE.md", "SAD template"),
                ("docs/ARCHITECTURE_GUIDE.md", "Architecture design guidelines"),
            ],
            4: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P4_SOP.md", "Phase 4 detailed steps"),
                ("docs/TEST_PLAN_TEMPLATE.md", "Test plan template"),
                ("docs/TEST_RESULTS_TEMPLATE.md", "Test results template"),
                ("docs/QA_GUIDE.md", "QA testing guidelines"),
            ],
            5: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P5_SOP.md", "Phase 5 detailed steps"),
                ("docs/BASELINE_TEMPLATE.md", "Baseline template"),
                ("docs/MONITORING_PLAN_TEMPLATE.md", "Monitoring plan template"),
                ("docs/VERIFICATION_GUIDE.md", "Verification guidelines"),
            ],
            6: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P6_SOP.md", "Phase 6 detailed steps"),
                ("docs/QUALITY_REPORT_TEMPLATE.md", "Quality report template"),
                ("docs/QUALITY_ASSURANCE_GUIDE.md", "Quality assurance guidelines"),
            ],
            7: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P7_SOP.md", "Phase 7 detailed steps"),
                ("docs/RISK_REGISTER_TEMPLATE.md", "Risk register template"),
                ("docs/RISK_MANAGEMENT_GUIDE.md", "Risk management guidelines"),
            ],
            8: [
                ("SKILL_DOMAIN.md", "Domain knowledge"),
                ("docs/P8_SOP.md", "Phase 8 detailed steps"),
                ("docs/CONFIG_RECORDS_TEMPLATE.md", "Configuration records template"),
                ("docs/CONFIGURATION_GUIDE.md", "Configuration management guidelines"),
            ],
        }
        
        rows = docs.get(phase, docs.get(3, []))
        if not rows:
            return "*(no document data)*"
        
        lines = ["| Document | Purpose |", "|------|------|"]
        for doc, usage in rows:
            lines.append(f"| `{doc}` | {usage} |")
        
        return "\n".join(lines)

    def _generate_fr_detailed_tasks_placeholder(self, frs: list, modules: list, phase: int) -> str:
        """Generate FR detailed task placeholder (specific content needs SRS parsing)"""
        lines = []
        for fr in frs:
            fr_num = fr['fr'].lower().replace('-', '').replace('fr', '').zfill(2)
            mod_match = next((m for m in modules if m.get('fr', '').upper() == fr['fr'].upper()), None)
            module_name = mod_match.get('module', 'Unknown') if mod_match else 'Unknown'
            lines.append(f"### {fr['fr']} {module_name}")
            lines.append(f"""
**Task**: Implement {module_name}

**SRS §{fr['fr']} Requirement**:
> See `.methodology/plans/phase{phase}_FULL.md` or run `python3 scripts/generate_full_plan.py --phase {phase}`

**SAD §Module Mapping**:
- `{module_name}` class
- `{mod_match.get('file', 'N/A') if mod_match else 'N/A'}`

**Forbidden**:
- ❌ app/infrastructure/ (Phase 3+ use 03-development/infrastructure/)
- ❌ @covers: L1 Error
- ❌ @type: edge
""")
        return '\n'.join(lines)

    def _generate_quality_gate_commands(self, phase: int) -> str:
        """Generate Quality Gate commands for given Phase"""
        commands = {
            3: [
                ("# 1. TH-06 Constitution test coverage >90%", "python3 quality_gate/constitution/runner.py --type implementation"),
                ("# 2. TH-10 test pass rate =100%", "pytest tests/ -v"),
                ("# 3. TH-11 coverage >=70%", "pytest --cov=app/ -v"),
                ("# 4. TH-16 code<->SAD =100%", "python3 cli.py trace-check"),
                ("# 5. TH-15 Phase Truth >90%", "python3 cli.py phase-verify --phase 3"),
                ("# 6. HR-08 stage-pass", "python3 cli.py stage-pass --phase 3"),
                ("# 7. HR-02 FrameworkEnforcer BLOCK", "python3 cli.py enforce --level BLOCK"),
            ],
            4: [
                ("# 1. TH-12 coverage >=80%", "pytest --cov=app/ -v"),
                ("# 2. TH-10 =100%", "pytest tests/ -v"),
                ("# 3. TH-13 FR coverage =100%", "python3 cli.py trace-check"),
                ("# 4. TH-17 FR<->test >=90%", "python3 cli.py phase-verify --phase 4"),
            ],
            1: [
                ("# 1. TH-03 Constitution correctness =100%", "python3 quality_gate/constitution/runner.py --type srs"),
                ("# 2. TH-04 Constitution safety =100%", "python3 quality_gate/constitution/runner.py --type srs"),
                ("# 3. TH-14 specification completeness =100%", "python3 quality_gate/doc_checker.py"),
            ],
            2: [
                ("# 1. TH-03 Constitution correctness =100%", "python3 quality_gate/constitution/runner.py --type sad"),
                ("# 2. TH-04 Constitution safety =100%", "python3 quality_gate/constitution/runner.py --type sad"),
                ("# 3. TH-05 Constitution maintainability >90%", "python3 quality_gate/constitution/runner.py --type sad"),
            ],
        }
        
        cmds = commands.get(phase, [("# Phase-specific QG commands", "# Add based on phase")])
        result = ""
        for desc, cmd in cmds:
            result += f"{desc}\n{cmd}\n\n"
        return result

    def _generate_developer_prompt(self, fr: dict, phase: int, repo_path: str = "") -> str:
        """Generate Developer Prompt template (Phase-specific)"""
        phase_prompts = cli_phase_prompts.PHASE_PROMPTS.get(phase, cli_phase_prompts.PHASE_PROMPTS[3])
        
        # For Phase 3, handle FR-specific placeholders
        if phase == 3:
            fr_num = fr.get("fr", "FR-01").lower().replace("fr-", "").strip()
            template = phase_prompts["developer"]
            import re
            template = re.sub(r"\{fr_num\}", fr_num, template)
            template = re.sub(r"\{fr\['fr'\]\}", fr.get("fr", f"FR-{fr_num}"), template)
            template = re.sub(r"\{fr\['title'\]\}", fr.get("title", ""), template)
            file_path = fr.get("file", f"app/processing/{fr_num}.py")
            # Fixed: simple string replace (original regex was broken)
            # Search for template string AFTER fr_num substitution (with 01.py, etc.)
            template = template.replace(
                f"{{fr.get('file', 'app/processing/{fr_num}.py')}}",
                file_path
            )
            # Phase 3: substitute all remaining placeholders for PhaseHooksAdapter
            if phase == 3:
                import re
                fr_id = fr.get("fr", f"FR-{fr_num}")
                methodology_path = str(Path(__file__).parent)
                template = template.replace("{repo_path}", str(repo_path))
                template = template.replace("{methodology_path}", methodology_path)
                template = template.replace("{phase_num}", str(phase))
                template = template.replace("{fr_id}", fr_id)
            return template
        
        return phase_prompts["developer"]


    def _generate_reviewer_prompt(self, fr: dict, phase: int, repo_path: str = "") -> str:
        """Generate Reviewer Prompt template (Phase-specific)"""
        phase_prompts = cli_phase_prompts.PHASE_PROMPTS.get(phase, cli_phase_prompts.PHASE_PROMPTS[3])
        
        # For Phase 3, handle FR-specific placeholders
        if phase == 3:
            fr_num = fr.get("fr", "FR-01").lower().replace("fr-", "").strip()
            template = phase_prompts["reviewer"]
            import re
            template = re.sub(r"\{fr_num\}", fr_num, template)
            template = re.sub(r"\{fr\['fr'\]\}", fr.get("fr", f"FR-{fr_num}"), template)
            template = re.sub(r"\{fr\['title'\]\}", fr.get("title", ""), template)
            template = template.replace(f"{{fr.get('file', 'app/processing/{fr_num}.py')}}", fr.get("file", f"app/processing/{fr_num}.py"))
            # Phase 3: substitute all PhaseHooksAdapter placeholders
            fr_id = fr.get("fr", f"FR-{fr_num}")
            methodology_path = str(Path(__file__).parent)
            template = template.replace("{repo_path}", str(repo_path))
            template = template.replace("{methodology_path}", methodology_path)
            template = template.replace("{phase_num}", str(phase))
            template = template.replace("{fr_id}", fr_id)
            return template
        
        return phase_prompts["reviewer"]


    def _generate_subagent_management(self, phase: int) -> str:
        """Generate Sub-Agent Management section (Need-to-Know + On-Demand)"""
        config = cli_phase_subagent.get_subagent_config(phase)
        
        lines = ["## 9.5 Sub-Agent Management (Need-to-Know + On-Demand)", ""]
        lines.append(f"**Phase {phase}: {config['name']}**")
        lines.append("")
        
        # Agent roles
        lines.append("### Agent Roles")
        lines.append(f"- **Agent A ({config['agent_a']['role']})**: {config['agent_a']['task']}")
        lines.append(f"- **Agent B ({config['agent_b']['role']})**: {config['agent_b']['task']}")
        lines.append("")
        
        # Need-to-Know
        ntk = config.get("need_to_know", {})
        lines.append("### Need-to-Know (provide only necessary info)")
        lines.append("")
        lines.append("| File | Section | Reason |")
        lines.append("|------|------|------|")
        for item in ntk.get("read", []):
            lines.append(f"| {item['path']} | {item['section']} | {item['why']} |")
        lines.append("")
        lines.append(f"**Skip**: `{', '.join(ntk.get('skip', []))}`")
        lines.append(f"**Context**: {ntk.get('context', 'N/A')}")
        lines.append("")
        
        # On-Demand
        od = config.get("on_demand", {})
        lines.append("### On-Demand (request only when needed)")
        lines.append("")
        lines.append(f"- **Trigger Condition**: {od.get('trigger', 'N/A')}")
        lines.append(f"- **Request Target**: {od.get('request_to', 'N/A')}")
        lines.append(f"- **Format**: {od.get('format', 'N/A')}")
        lines.append("")
        
        # Tool Timing
        tt = config.get("tool_timing", {})
        lines.append("### Tool Call Timing")
        lines.append("")
        
        tool_events = [
            ("spawn", "Spawn Sub-agent"),
            ("knowledge_curator", "KnowledgeCurator"),
            ("context_manager", "ContextManager"),
            ("quality_gate", "Quality Gate"),
            ("checkpoint", "Checkpoint")
        ]
        
        lines.append("| Event | Tool | Arguments |")
        lines.append("|------|------|------|")
        for event, name in tool_events:
            if event in tt:
                params = str(tt[event].get('params', tt[event]))
                lines.append(f"| {event} | {name} | {params} |")
            else:
                lines.append(f"| {event} | {name} | - |")
        lines.append("")
        
        # Isolation
        iso = config.get("isolation", {})
        lines.append("### Isolation Method")
        lines.append("")
        lines.append(f"- **Method**: `{iso.get('method', 'N/A')}`")
        lines.append(f"- **Fresh Messages**: `{', '.join(iso.get('fresh_messages', [])) or '(empty)'}`")
        lines.append(f"- **Log Format**: `{iso.get('log_format', 'N/A')}`")
        lines.append("")
        
        return "\n".join(lines)


    def _generate_sessions_spawn_log_format(self, frs: list, phase: int) -> str:
        """Generate sessions_spawn.log format description"""
        if phase != 3:
            return "2 records per Phase (developer + reviewer)"
        
        lines = ["Each FR generates 2 records, total " + str(len(frs) * 2) + " records:"]
        lines.append("```json")
        for fr in frs[:2]:
            lines.append(f'{{"timestamp":"ISO8601","role":"developer","task":"{fr["fr"]} {fr["title"]}","session_id":"uuid","confidence":8,"commit":"HASH"}}')
            lines.append(f'{{"timestamp":"ISO8601","role":"reviewer","task":"{fr["fr"]} Review","session_id":"uuid","confidence":9,"verdict":"APPROVE"}}')
        lines.append("...")
        lines.append("```")
        return "\n".join(lines)

    def cmd_plan_phase(self, args):
        """
        Generate execution plan for Phase

        This command (refactored v6.106):
        - ✅ scans all relevant data (SKILL.md / SOP / state.json / historical iterations)
        - ✅ generates Markdown format execution plan for main agent
        - ✅ supports --repair for fix iterations
        - ✅ supports --history to view history
        - ❌ does not run Pre-flight check (delegated to run-phase)

        Separation of concerns:
        - plan-phase: generate plan
        - run-phase: Pre-flight check + execute
        """
        import sys
        import json
        from pathlib import Path
        from datetime import datetime

        phase = args.phase
        goal = args.goal or f"Phase {phase} execution"
        repo_path = Path(args.repo or Path.cwd())
        with_timeline = getattr(args, 'with_timeline', False)

        print(f"\n{'='*60}")
        print(f"📋 plan-phase --phase {phase}")
        print(f"{'='*60}\n")

        # === PHASE 1: SCAN ===
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SCAN: reading relevant data")

        # 1.1 must scan (Framework artifacts, not Project)
        METHODOLOGY_DIR = Path(__file__).parent.resolve()
        skill_md = METHODOLOGY_DIR / "SKILL.md"
        sop_path = METHODOLOGY_DIR / "docs" / f"P{phase}_SOP.md"
        state_file = repo_path / ".methodology" / "state.json"
        iterations_file = repo_path / ".methodology" / "iterations" / f"phase{phase}.json"

        if not skill_md.exists():
            print(f"❌ SKILL.md not found at {skill_md}")
            return 1
        if not sop_path.exists():
            print(f"❌ P{phase}_SOP.md not found at {sop_path}")
            return 1

        skill_content = skill_md.read_text()
        sop_content = sop_path.read_text()
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        iterations = json.loads(iterations_file.read_text()) if iterations_file.exists() else {}

        print(f"✅ SKILL.md loaded ({len(skill_content)} chars)")
        print(f"✅ P{phase}_SOP.md loaded ({len(sop_content)} chars)")

        # 1.2 scan artifacts by Phase
        phase_deliverables = {
            1: ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md"],
            2: ["SRS.md", "SAD.md", "ADR.md"],
            3: ["SRS.md", "SAD.md", "app/processing/"],
            4: ["SRS.md", "SAD.md", "TEST_PLAN.md"],
            5: ["BASELINE.md", "MONITORING_PLAN.md"],
            6: ["QUALITY_REPORT.md"],
            7: ["RISK_REGISTER.md"],
            8: ["CONFIG_RECORDS.md", "requirements.lock"],
        }
        # Check deliverables - search patterns
        deliverables = phase_deliverables.get(phase, [])
        missing_deliverables = []
        for d in deliverables:
            # Special handling for SAD.md - search in common locations
            if d == "SAD.md":
                if (repo_path / "SAD.md").exists():
                    continue
                if (repo_path / "02-architecture" / "SAD.md").exists():
                    continue
                if (repo_path / "templates" / "SAD.md").exists():
                    continue
                missing_deliverables.append(d)
            # Skip Phase-specific output directories (app/processing, tests, etc.)
            elif d.startswith("app/") or d.startswith("tests/"):
                # These are expected outputs, not prerequisites
                continue
            else:
                d_path = repo_path / d
                if not d_path.exists():
                    missing_deliverables.append(d)
        if missing_deliverables:
            print(f"⚠️  Missing deliverables: {missing_deliverables}")

        # === PHASE 2: GENERATE PLAN ===
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] PLAN: generating execution plan")

        # Read project context
        frs = self._parse_srs_fr_list(repo_path)
        modules = self._parse_sad_modules(repo_path)

        # Determine HR/TH based on phase
        hr_map = {
            1: ["HR-01", "HR-04", "HR-06", "HR-07", "HR-08", "HR-10", "HR-11"],
            2: ["HR-01", "HR-04", "HR-06", "HR-07", "HR-08", "HR-10", "HR-11"],
            3: ["HR-01", "HR-04", "HR-06", "HR-07", "HR-08", "HR-10", "HR-11", "HR-15"],
            4: ["HR-01", "HR-04", "HR-08", "HR-10", "HR-11", "HR-12"],
            5: ["HR-01", "HR-04", "HR-08", "HR-10"],
            6: ["HR-01", "HR-07", "HR-08", "HR-10"],
            7: ["HR-01", "HR-08", "HR-10", "HR-12"],
            8: ["HR-01", "HR-08", "HR-10", "HR-12"],
        }
        th_map = {
            1: ["TH-01", "TH-03", "TH-04", "TH-08", "TH-14", "TH-15"],
            2: ["TH-01", "TH-03", "TH-04", "TH-05", "TH-08", "TH-15"],
            3: ["TH-06", "TH-08", "TH-09", "TH-10", "TH-11", "TH-15", "TH-16"],
            4: ["TH-01", "TH-03", "TH-04", "TH-06", "TH-10", "TH-12", "TH-13", "TH-15", "TH-17"],
            5: ["TH-02", "TH-07"],
            6: ["TH-02", "TH-07"],
            7: ["TH-07"],
            8: ["TH-02"],
        }
        role_map = {
            1: ("architect", "reviewer"),
            2: ("architect", "reviewer"),
            3: ("developer", "reviewer"),
            4: ("qa", "reviewer"),
            5: ("devops", "architect"),
            6: ("qa", "architect"),
            7: ("qa", "pm"),
            8: ("devops", "pm"),
        }
        agent_a, agent_b = role_map.get(phase, ("developer", "reviewer"))

        # Generate FR-by-FR table (only for Phase 3)
        fr_table = self._generate_fr_table(frs, modules) if phase == 3 else ""
        qg_commands = self._generate_quality_gate_commands(phase)
        # === PHASE 3: GENERATE PLAN FROM TEMPLATE ===
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] TEMPLATE: reading plan_phase_template.md")
        # Read template - use phase-specific template if available
        if phase == 6:
            template_path = Path(__file__).parent / "templates" / "plan_phase_6_template.md"
        else:
            template_path = Path(__file__).parent / "templates" / "plan_phase_template.md"
        
        if not template_path.exists():
            print(f"⚠️  Template not found, using inline generation")
            template_content = None
        else:
            template_content = template_path.read_text(encoding='utf-8')
        
        # Generate supporting data
        # Generate FR table rows only for Phase 3
        fr_table_rows = self._generate_fr_table(frs, modules) if phase == 3 else ""
        log_format = self._generate_sessions_spawn_log_format(frs, phase)
        subagent_mgmt = self._generate_subagent_management(phase)
        
        # Generate phase-specific four-dimensional tables
        four_dimensional_table = cli_phase_subagent.get_four_dimensional_table(phase)
        iteration_rounds_table = cli_phase_subagent.get_iteration_rounds_table(phase)
        
        # Generate Phase artifacts summary (P0: Aspect 3 - previous phase output handover)
        from parse_phase_artifacts import get_artifacts_summary
        artifacts_summary = get_artifacts_summary(phase, repo_path)
        
        # NOTE (v6.107): Pre-flight checks moved to run-phase.
        # plan-phase only generates the execution plan.
        pf_note = "_(Pre-flight checks handled by run-phase)_"
        
        # Build deliverables
        deliv_lines = []
        for d in deliverables:
            deliv_lines.append(f"- [ ] `{d}`")
        
        if template_content:
            # Use template
            plan = template_content
            plan = plan.replace('{PHASE}', str(phase))
            plan = plan.replace('{PHASE_PLUS_1}', str(phase + 1))
            # Read VERSION dynamically
            version_file = Path(__file__).parent / "VERSION"
            current_version = version_file.read_text().strip() if version_file.exists() else "v7.19"
            plan = plan.replace('{VERSION}', current_version)
            plan = plan.replace('{PROJECT_NAME}', state.get('project_name', repo_path.name))
            plan = plan.replace('{DATE}', datetime.now().strftime('%Y-%m-%d'))
            plan = plan.replace('{GOAL}', goal)
            plan = plan.replace('{HR_LIST}', ' | '.join(hr_map.get(phase, [])))
            plan = plan.replace('{TH_LIST}', ' | '.join(th_map.get(phase, [])))
            
            # Agent roles for A/B collaboration
            plan = plan.replace('{AGENT_A}', agent_a)
            plan = plan.replace('{AGENT_B}', agent_b)
            plan = plan.replace('{AGENT_A_UPPER}', agent_a.upper())
            plan = plan.replace('{AGENT_B_UPPER}', agent_b.upper())
            
            # Enrich FR with module info from SAD
            first_fr = frs[0] if frs else {'fr': 'FR-01', 'title': 'Task', 'module': 'module', 'file': 'app/processing/01.py'}
            mod_match = next((m for m in modules if m.get("fr", "").upper() == first_fr["fr"].upper()), None)
            if mod_match:
                first_fr['module'] = mod_match.get("module", first_fr.get('module', 'module'))
                first_fr['file'] = mod_match.get("file", first_fr.get('file', ''))
            
            # Developer and Reviewer prompts (with enriched FR info)
            developer_prompt = self._generate_developer_prompt(first_fr, phase, str(repo_path))
            reviewer_prompt = self._generate_reviewer_prompt(first_fr, phase, str(repo_path))
            plan = plan.replace('{DEVELOPER_PROMPT}', developer_prompt)
            plan = plan.replace('{REVIEWER_PROMPT}', reviewer_prompt)
            
            plan = plan.replace('{FR_COUNT}', str(len(frs)))
            # Phase-specific content for §3 FR-by-FR table
            if phase == 3:
                plan = plan.replace('{FR_TABLE_ROWS}', fr_table_rows)
            elif phase == 6:
                plan = plan.replace('{FR_TABLE_ROWS}', '*(Phase 6 does not need FR table, focus on quality evaluation)*')
            else:
                plan = plan.replace('{FR_TABLE_ROWS}', '*(this Phase does not need FR table)*')
            plan = plan.replace('{QG_COMMANDS}', qg_commands)
            plan = plan.replace('{SESSION_LOG_EXAMPLE}', log_format)
            plan = plan.replace('{TOTAL_RECORDS}', str(len(frs) * 2))
            plan = plan.replace('{PREFLIGHT_RESULTS}', pf_note)
            
            # Phase-specific deliverable structure
            if phase == 6:
                q6 = "### Phase 6 Output Structure\n\n"
                q6 += "```\n06-quality/\n"
                q6 += "├── QUALITY_REPORT.md      # Quality Report\n"
                q6 += "└── MONITORING_PLAN.md    # Monitoring Plan\n```\n\n"
                q6 += "### Phase 6 Artifact Checklist\n\n"
                q6 += "- [ ] `06-quality/QUALITY_REPORT.md` - Quality Dimension Evaluation Report\n"
                q6 += "- [ ] `06-quality/MONITORING_PLAN.md` - Monitoring Plan\n"
                q6 += "- [ ] Constitution score >= 80%\n"
                q6 += "- [ ] Logic correctness >= 90\n"
                q6 += "- [ ] Phase Truth >90%\n"
                deliverable_structure = q6
            else:
                deliverable_structure = self._generate_deliverable_structure(frs, modules)
            plan = plan.replace('{DELIVERABLE_STRUCTURE}', deliverable_structure)
            plan = plan.replace('{DELIVERABLE_CHECKLIST}', self._build_deliverable_checklist(phase, frs, modules))
            
            # Phase-specific §5 content
            if phase == 6:
                q6 = "## 5. Quality Evaluation Tasks (4 total)\n\n"
                q6 += "### 5.1 Quality Dimension Definitions\n\n"
                q6 += "| Dimension | Metric | Target | Verification |\n"
                q6 += "|------|------|--------|---------|\n"
                q6 += "| Maintainability | Constitution score | >= 80% | constitution runner |\n"
                q6 += "| Logic Correctness | Logic correctness score | >= 90 | phase-verify |\n"
                q6 += "| Test Coverage | Coverage | >= 80% | pytest --cov |\n"
                q6 += "| Phase Truth | Phase Truth score | >90% | phase-verify |\n\n"
                q6 += "### 5.2 Quality Evaluation Tasks\n\n"
                q6 += "| Task | Owner | Input | Output |\n"
                q6 += "|------|------|------|------|\n"
                q6 += "| Quality dimension data collection | Agent A (qa) | TEST_RESULTS.md, BASELINE.md | Quality data |\n"
                q6 += "| Constitution check | Agent A (qa) | All Phase outputs | Check report |\n"
                q6 += "| Logic correctness verification | Agent B (architect) | Code, TEST_RESULTS | Verification report |\n"
                q6 += "| QUALITY_REPORT writing | Agent A (qa) | All check results | QUALITY_REPORT.md |\n\n"
                q6 += "### 5.3 Quality Thresholds\n\n"
                q6 += "| TH | Metric | Threshold | Verify Command |\n"
                q6 += "|----|------|------|---------|\n"
                q6 += "| TH-02 | Constitution total score | >= 80% | `constitution/runner.py --type all` |\n"
                q6 += "| TH-07 | Logic correctness | >= 90 | `phase-verify` |\n"
                plan = plan.replace('{FR_DETAILED_TASKS}', q6)
            elif not getattr(args, 'detailed', False):
                plan = plan.replace('{FR_DETAILED_TASKS}', self._generate_fr_detailed_tasks_placeholder(frs, modules, phase))
            else:
                plan = plan.replace('{FR_DETAILED_TASKS}', '')
            
            plan = plan.replace('{TH_THRESHOLDS_TABLE}', self._generate_thresholds_table(phase))
            plan = plan.replace('{EXTERNAL_DOCS}', self._generate_external_docs(phase))
            plan = plan.replace('{subagent_mgmt}', subagent_mgmt)
            plan = plan.replace('{artifacts_summary}', artifacts_summary)
            plan = plan.replace('{four_dimensional_table}', four_dimensional_table)
            plan = plan.replace('{iteration_rounds_table}', iteration_rounds_table)
        # NOTE: Inline generation fallback (_generate_plan_fallback) removed in v6.107.
        # plan-phase now uses template-based generation only.
        # If template is missing, the code will error at template_content read above.
        # Print plan
        print(plan)

        # Save plan to file
        plan_file = repo_path / ".methodology" / "plans" / f"phase{phase}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(plan)
        print(f"\n💾 Plan saved: {plan_file}")

        # Generate FR detailed tasks inline if --detailed flag is set
        if getattr(args, 'detailed', False):
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] DETAILED: generating FR detailed tasks (merging into main plan)...")
            try:
                import subprocess
                # Run generate_full_plan.py and capture output
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).parent / "scripts" / "generate_full_plan.py"),
                     "--phase", str(phase), "--repo", str(repo_path), "--no-output"],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0 and result.stdout:
                    # Parse FR detailed tasks from output (skip header lines)
                    full_plan_content = result.stdout
                    # Extract just the task sections (skip header/banner)
                    lines = full_plan_content.split('\n')
                    task_start = 0
                    for i, line in enumerate(lines):
                        if '## Phase' in line and i > 0:
                            task_start = i
                            break
                    fr_tasks = '\n'.join(lines[task_start:]) if task_start else full_plan_content
                    
                    # Insert FR tasks after section 5 header
                    # Find "## 5. FR Detailed Tasks" and insert after it
                    section_marker = f"## 5. FR Detailed Tasks"
                    marker_idx = plan.find(section_marker)
                    if marker_idx != -1:
                        # Find end of this section (next ## at same level or end of file)
                        insert_pos = plan.find('\n## ', marker_idx + len(section_marker))
                        if insert_pos == -1:
                            insert_pos = len(plan)
                        plan = plan[:insert_pos] + '\n\n' + fr_tasks + plan[insert_pos:]
                        # Re-save the merged plan
                        plan_file.write_text(plan)
                        print(f"✅ FR detailed tasks merged to: {plan_file}")
                    else:
                        print(f"⚠️ FR Detailed Tasks section not found")
                else:
                    print(f"⚠️ FR detailed task generation failed")
            except Exception as e:
                print(f"⚠️ FR detailed task generation exception: {e}")

        # NOTE (v6.108): Pre-flight checks moved to run-phase.
        # plan-phase only generates the plan; run-phase validates.
        print(f"\n✅ Plan generated successfully")
        print(f"   Pre-flight checks are handled by 'run-phase'")
        return 0

    # ============================================================================
    # Session Commands
    # ============================================================================

    def cmd_session_save(self, args):
        """Save complete session state"""
        session_id = args.id
        state_file = args.state or ".methodology/session_state.json"

        # Read session state
        state_path = Path(state_file)
        if not state_path.exists():
            print(f"❌ State file not found: {state_file}")
            print("   Hint: Use --state to specify a different state file")
            return 1

        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse state file: {e}")
            return 1

        # Validate required fields
        required = ["messages", "context", "artifacts", "metadata"]
        missing = [k for k in required if k not in state]
        if missing:
            print(f"⚠️  State missing fields: {', '.join(missing)}")
            print("   These fields are recommended but not required")

        # Save
        path = self.session_manager.save(session_id, state)
        print(f"✅ Session saved: {session_id}")
        print(f"   Path: {path}")
        return 0

    def cmd_session_load(self, args):
        """Restore complete session state"""
        session_id = args.id
        output_file = args.output or ".methodology/session_state_loaded.json"

        # Check if exists
        if not self.session_manager.exists(session_id):
            print(f"❌ Session not found: {session_id}")
            print("   Hint: Use 'session-list' to see available sessions")
            return 1

        try:
            state = self.session_manager.load(session_id)
            info = self.session_manager.get_info(session_id)

            # Write to file
            Path(output_file).write_text(json.dumps(state, indent=2, ensure_ascii=False))

            print(f"✅ Session loaded: {session_id}")
            print(f"   Saved at: {info['saved_at']}")
            print(f"   Size: {info['size']} bytes")
            print(f"   Output: {output_file}")
            return 0
        except Exception as e:
            print(f"❌ Failed to load session: {e}")
            return 1

    def cmd_session_list(self, args):
        """List all saved sessions"""
        sessions = self.session_manager.list()

        if not sessions:
            print("No saved sessions found.")
            print("   Hint: Use 'session-save --id <session_id>' to save a session")
            return 0

        print(f"\n╔══════════════════════════════════════════════════════════════════════╗")
        print(f"║  Saved Sessions ({len(sessions)})                                                ║")
        print(f"╠══════════════════════════════════════════════════════════════════════╣")
        print(f"║  {'Session ID':<30} {'Saved At':<28} {'Size':>10}  ║")
        print(f"╠══════════════════════════════════════════════════════════════════════╣")

        for s in sessions:
            saved_at = s.get('saved_at', 'unknown')
            if saved_at != 'unknown':
                saved_at = saved_at[:19]  # Truncate to YYYY-MM-DDTHH:MM:SS
            size_kb = s['size'] / 1024
            print(f"║  {s['id']:<30} {saved_at:<28} {size_kb:>8.1f} KB  ║")

        print(f"╚══════════════════════════════════════════════════════════════════════╝")
        print(f"\nUsage:")
        print(f"  python cli.py session-load --id <session_id>   # Load a session")
        print(f"  python cli.py session-delete --id <session_id>  # Delete a session")
        return 0

    def cmd_session_delete(self, args):
        """Delete specified session"""
        session_id = args.id

        if not self.session_manager.exists(session_id):
            print(f"❌ Session not found: {session_id}")
            return 1

        if self.session_manager.delete(session_id):
            print(f"✅ Session deleted: {session_id}")
            return 0
        else:
            print(f"❌ Failed to delete session: {session_id}")
            return 1

    def cmd_context_compress(self, args):
        """Context Compression - three-layer compression"""
        import context_compressor
        import json
        from pathlib import Path
        
        repo_path = Path(args.repo or Path.cwd())
        msg_file = repo_path / ".methodology" / "session_messages.json"
        
        if not msg_file.exists():
            print("No session_messages.json found")
            return 1
        
        messages = json.loads(msg_file.read_text())
        level = getattr(args, 'level', 'auto')
        
        compressed = context_compressor.compress(messages, level)
        msg_file.write_text(json.dumps(compressed, indent=2))
        print(f"Compressed {len(messages)} → {len(compressed)} messages")
        return 0

    def cmd_update_project_status(self, args):
        """Update 'Next Actions' block in PROJECT_STATUS.md

        Usage:
            python cli.py update-project-status --step 4.1 --module SynthEngine --action "Complete TC tracking"
        """
        from pathlib import Path

        repo_path = Path(args.repo or ".")
        status_file = repo_path / "PROJECT_STATUS.md"

        step = args.step
        module = args.module
        action = args.action
        status_value = args.status or "in-progress"

        if not status_file.exists():
            pass # Removed print-debug
            return 1

        try:
            content = status_file.read_text(encoding="utf-8")
        except IOError as e:
            pass # Removed print-debug
            return 1

        # Parse and update 'Next Actions' table
        # Format: | Step | Module | Action | Status |
        lines = content.split('\n')
        updated = False
        in_next_actions_section = False

        for i, line in enumerate(lines):
            # Detect entry into 'Next Actions' block
            if "Next Actions" in line:
                in_next_actions_section = True
                continue

            # End if another section header is encountered
            if in_next_actions_section and line.startswith("#"):
                in_next_actions_section = False

            # In Next Actions block, find row matching step/module and update status
            if in_next_actions_section and "|" in line and step in line and module in line:
                # Parse status column and update
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    # Find Status column (second to last)
                    parts[-2] = status_value
                    lines[i] = "|" + "|".join(f" {p} " for p in parts) + "|"
                    updated = True
                    break

        if updated:
            try:
                status_file.write_text("\n".join(lines), encoding="utf-8")
                pass # Removed print-debug
                return 0
            except IOError as e:
                pass # Removed print-debug
                return 1
        else:
            pass # Removed print-debug
            return 1


    def cmd_tool_registry(self, args):
        """Tool Registry CLI - Clawd-Code TOOL_HANDLERS style tool management

        Usage:
            python cli.py tool-registry --list                      # list all tools
            python cli.py tool-registry --register Read --handler read_file   # register tool
            python cli.py tool-registry --unregister Read           # unregister
            python cli.py tool-registry --get Read                  # query tool handler
            python cli.py tool-registry --dispatch Read --path /tmp/test.txt  # dispatch call
        """
        action = args.action

        if action == "list" or args.list:
            tools = ToolRegistry.list_tools()
            pass # Removed print-debug
            pass # Removed print-debug
            for tool in tools:
                handler = ToolRegistry.get_handler(tool)
                kind = "handler" if handler else "factory"
                pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif action == "register" or (args.register and args.handler):
            tool_name = args.register or args.tool_name
            handler_path = args.handler or args.handler_path

            if not tool_name or not handler_path:
                pass # Removed print-debug
                return 1

            # Attempt to load handler from module
            try:
                import importlib
                if '.' in handler_path:
                    module_path, func_name = handler_path.rsplit('.', 1)
                    module = importlib.import_module(module_path)
                    handler = getattr(module, func_name)
                else:
                    # Assume it is a built-in or imported function name
                    # Security fix: use getattr instead of eval for handler lookup
                    parts = handler_path.rsplit('.', 1)
                    if len(parts) == 2:
                        module_name, func_name = parts
                        import importlib
                        mod = importlib.import_module(module_name)
                        handler = getattr(mod, func_name)
                    else:
                        handler = None
            except Exception as e:
                pass # Removed print-debug
                return 1

            ToolRegistry.register(tool_name, handler)
            pass # Removed print-debug
            pass # Removed print-debug
            return 0

        elif action == "unregister" or args.unregister:
            tool_name = args.unregister or args.tool_name
            if not tool_name:
                pass # Removed print-debug
                return 1

            removed = ToolRegistry.unregister(tool_name)
            if removed:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
            return 0

        elif action == "get" or args.get:
            tool_name = args.get or args.tool_name
            if not tool_name:
                pass # Removed print-debug
                return 1

            handler = ToolRegistry.get_handler(tool_name)
            if handler:
                pass # Removed print-debug
            else:
                pass # Removed print-debug
                pass # Removed print-debug
            return 0

        elif action == "dispatch" or args.dispatch:
            tool_name = args.dispatch or args.tool_name
            if not tool_name:
                pass # Removed print-debug
                return 1

            # Parse parameters from --kwargs
            kwargs = {}
            if args.kwargs:
                for kv in args.kwargs:
                    if '=' in kv:
                        k, v = kv.split('=', 1)
                        kwargs[k] = v

            try:
                result = ToolRegistry.dispatch(tool_name, **kwargs)
                pass # Removed print-debug
            except KeyError as e:
                pass # Removed print-debug
                return 1
            except Exception as e:
                pass # Removed print-debug
                return 1
            return 0

        else:
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            pass # Removed print-debug
            return 1

    def cmd_onboarding(self, args):
        """Interactive Onboarding Wizard — Phase-gate workflow guide (P3-3)"""
        from pathlib import Path
        import sys

        project_path = Path(args.project)
        phase = args.phase
        resume = args.resume
        list_phases = args.list_phases

        # List phases mode
        if list_phases:
            import yaml
            checkpoints_dir = Path(__file__).parent / "onboarding" / "checkpoints"
            for p in [1, 2, 3]:
                cp_file = checkpoints_dir / f"phase{p}.yaml"
                if cp_file.exists():
                    with open(cp_file) as f:
                        data = yaml.safe_load(f)
                    click.echo(f"\nPhase {p}: {data.get('name', 'Unknown')}")
                    for cp in data.get("checkpoints", []):
                        click.echo(f"  • {cp['title']}")
                else:
                    click.echo(f"\nPhase {p}: (no checkpoint file)")
            return 0

        # Validate phase range
        if phase not in (1, 2, 3):
            click.echo("❌ Only phases 1, 2, and 3 are currently supported.")
            return 1

        # Show context
        click.echo(f"\n🚀 Onboarding Wizard — Phase {phase}")
        click.echo(f"   Project: {project_path.absolute()}")
        if resume:
            click.echo(f"   Mode: RESUME (picking up from last checkpoint)")

        # Run the wizard
        result = run_phase(phase, project_path, resume=resume)
        return result

    def cmd_steering(self, args):
        """Steering Loop - AB Workflow direction control engine CLI"""
        action = args.steering_action
        project_path = Path(args.project) if hasattr(args, 'project') and args.project else Path.cwd()
        history_path = str(project_path / ".methodology" / "steering_history.json")

        # Helper: Load state.json for current phase
        state_path = project_path / ".methodology" / "state.json"
        current_phase = None
        ab_rounds = 0
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                current_phase = state.get("current_phase")
                ps = state.get("phase_state", {})
                ab_rounds = ps.get("ab_rounds", 0)
            except Exception:
                pass

        if action == "status":
            # Show current Steering status
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Steering Status                                              ║
╠══════════════════════════════════════════════════════════════╣""")
            print(f"║  Current Phase: {current_phase or 'N/A':<38}║")
            print(f"║  A/B Rounds (recorded): {ab_rounds:<30}║")

            if Path(history_path).exists():
                try:
                    history = json.loads(Path(history_path).read_text(encoding="utf-8"))
                    iterations = history.get("iterations", [])
                    best_score = history.get("best_score")
                    print(f"║  Steering History: {len(iterations)} rounds{' '*30}║")
                    if best_score is not None:
                        print(f"║  Best Score: {best_score:<43}║")
                except Exception:
                    print("║  Steering History: (parse error)" + " " * 28 + "║")
            else:
                print("║  Steering History: (no data)" + " " * 30 + "║")

            print("╠══════════════════════════════════════════════════════════════╣")
            print("║  Description:                                                 ║")
            print("║    steering run --phase N  - run Steering Loop guidance        ║")
            print("║    steering status        - show current Steering status      ║")
            print("║    steering history       - show guidance history             ║")
            print("╚══════════════════════════════════════════════════════════════╝")
            return 0

        elif action == "history":
            # Show Steering guidance history
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Steering History                                             ║
╠══════════════════════════════════════════════════════════════╣""")

            if not Path(history_path).exists():
                print("║  No steering history found." + " " * 33 + "║")
                print("║  Run 'steering run --phase N' to start." + " " * 24 + "║")
                print("╚══════════════════════════════════════════════════════════════╝")
                return 0

            try:
                history = json.loads(Path(history_path).read_text(encoding="utf-8"))
                iterations = history.get("iterations", [])
                best_score = history.get("best_score")

                if not iterations:
                    print("║  No iterations recorded yet." + " " * 31 + "║")
                else:
                    print(f"║  Total iterations: {len(iterations):<37}║")
                    if best_score is not None:
                        print(f"║  Best score: {best_score:<43}║")
                    print("╠══════════════════════════════════════════════════════════════╣")
                    print("║  Iterations:                                                   ║")
                    for i, it in enumerate(iterations, 1):
                        stage_icon = {"exploration": "🔍", "competition": "⚔️", "convergence": "🎯"}.get(it.get("stage", ""), "⚪")
                        delta_str = f"delta={it.get('score_delta', 0):.4f}"
                        convergence_str = f"cv={it.get('convergence_score', 0):.4f}"
                        winner = it.get("winner", "?")
                        print(f"║    {i}. {stage_icon} Round {it.get('iteration', i)}: winner={winner}, {delta_str}, {convergence_str}   ║")
            except Exception as e:
                print(f"║  Error reading history: {e}" + " " * 29 + "║")

            print("╚══════════════════════════════════════════════════════════════╝")
            return 0

        elif action == "run":
            # Run Steering Loop guidance
            phase = args.phase
            max_rounds = args.max_rounds

            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Steering Loop Execution                                      ║
╠══════════════════════════════════════════════════════════════╣""")
            print(f"║  Project: {project_path.name:<46}║")
            print(f"║  Phase: {phase:<48}║")
            print(f"║  Max rounds: {max_rounds or 'config default':<41}║")

            if not state_path.exists():
                print("╠══════════════════════════════════════════════════════════════╣")
                print("║  ❌ No .methodology/state.json found.                        ║")
                print("║     Initialize project first with: python cli.py init        ║")
                print("╚══════════════════════════════════════════════════════════════╝")
                return 1

            # Check required files
            required = {"SRS.md": "Phase 1-2", "SAD.md": "Phase 2"}
            missing = [f for f in required if not (project_path / f).exists()]
            if missing:
                print(f"╠══════════════════════════════════════════════════════════════╣")
                print(f"║  ⚠️  Missing files (may be normal for early phases):          ║")
                for f in missing:
                    print(f"║     - {f:<49}║")

            print("╠══════════════════════════════════════════════════════════════╣")
            print("║  📋 Steering Loop Description:                                ║")
            print("║                                                               ║")
            print("║    1. Requires LLM Provider (e.g. OpenAI provider)            ║")
            print("║    2. SteeringLoop requires A/B two outputs to iterate        ║")
            print("║    3. Direct CLI calls suit integration with external agents  ║")
            print("║                                                               ║")
            print("║  🔧 Usage (integrate into agent workflow):                    ║")
            print("║    from steering import SteeringLoop, SteeringConfig            ║")
            print("║    loop = SteeringLoop(provider, config=SteeringConfig())     ║")
            print("║    result = loop.iterate(output_a, output_b)                  ║")
            print("║    continue_it, reason = loop.should_continue()               ║")
            print("╚══════════════════════════════════════════════════════════════╝")

            # Attempt to initialize SteeringLoop (requires provider)
            # Note: cannot assume LLM provider in CLI environment, printing description only
            # Real execution requires external system integration

            return 0

        else:
            print(f"❌ Unknown steering action: {action}")
            print("   Available: run, status, history")
            return 1


# ==================== Main ====================

def main():
    parser = argparse.ArgumentParser(
        description="Methodology - AI Agent Development Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # init
    init_parser = subparsers.add_parser("init", help="Initialize project")
    init_parser.add_argument("name", nargs="?", help="Project name")
    
    # finish
    subparsers.add_parser("finish", help="Finish project and stop quality watch")
    
    # task
    task_parser = subparsers.add_parser("task", help="Task management")
    task_parser.add_argument("action", choices=["add", "list", "complete", "sprint"],
                           help="Task action")
    task_parser.add_argument("title", nargs="?", help="Task title")
    task_parser.add_argument("--points", type=int, help="Story points")
    task_parser.add_argument("--priority", type=int, help="Priority (1-5)")
    task_parser.add_argument("--task-id", help="Task ID")
    task_parser.add_argument("--sprint-id", help="Sprint ID")
    
    # sprint
    sprint_parser = subparsers.add_parser("sprint", help="Sprint management")
    sprint_parser.add_argument("action", choices=["create", "list", "start"],
                             help="Sprint action")
    sprint_parser.add_argument("name", nargs="?", help="Sprint name")
    sprint_parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    sprint_parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    sprint_parser.add_argument("--capacity", type=int, help="Capacity (points)")
    sprint_parser.add_argument("--sprint-id", help="Sprint ID")
    
    # board
    board_parser = subparsers.add_parser("board", help="Open project board")
    board_parser.add_argument("--sprint-id", help="Sprint ID")
    
    # report
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("--type", choices=["daily", "weekly", "monthly"],
                             help="Report type")
    report_parser.add_argument("--output", "-o", help="Output file")
    
    # status
    subparsers.add_parser("status", help="Show status")
    
    # phase-status: Phase execution status
    phase_status_parser = subparsers.add_parser("phase-status", help="Show Phase execution status")
    phase_status_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    
    # phase-pause: Pause Phase execution
    phase_pause_parser = subparsers.add_parser("phase-pause", help="Pause Phase execution")
    phase_pause_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    
    # phase-resume: Resume Phase execution
    phase_resume_parser = subparsers.add_parser("phase-resume", help="Resume Phase execution")
    phase_resume_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    
    # phase-freeze: Freeze project (HR-14)
    subparsers.add_parser("phase-freeze", help="Freeze project (HR-14: Integrity < 40)")
    
    # ab-history: A/B round history
    ab_history_parser = subparsers.add_parser("ab-history", help="Show A/B round history")
    ab_history_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    
    # ab-record: record one A/B round-trip
    ab_record_parser = subparsers.add_parser("ab-record", help="Record an A/B round")
    ab_record_parser.add_argument("--phase", type=int, required=True, help="Phase number")
    ab_record_parser.add_argument("--notes", type=str, default="", help="Notes (optional)")
    ab_record_parser.add_argument("--project", type=str, default=".", help="Project path")
    
    # audit-heatmap: Cross-project failure heatmap
    subparsers.add_parser("audit-heatmap", help="Show cross-project failure heatmap")
    
    # time-check: Phase duration check
    time_check_parser = subparsers.add_parser("time-check", help="Check Phase duration")
    time_check_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    time_check_parser.add_argument("--threshold", type=int, default=120, help="Threshold in minutes (default: 120)")
    
    # bus
    bus_parser = subparsers.add_parser("bus", help="Message Bus")
    bus_parser.add_argument("action", choices=["status", "tree", "watch"],
                           help="Bus action")
    bus_parser.add_argument("--seconds", type=int, help="Watch duration")

    # constitution
    constitution_parser = subparsers.add_parser("constitution", help="Constitution")
    constitution_parser.add_argument("subcommand", nargs="?", default="view",
                                   choices=["view", "thresholds", "errors", "check", "edit", "compile", "verify"],
                                   help="Constitution subcommand")
    constitution_parser.add_argument("output", nargs="?", help="Output text to verify (for verify subcommand)")
    constitution_parser.add_argument("--auto-fix", action="store_true", help="auto-fix known issues (for check subcommand)")
    constitution_parser.add_argument("--skip-failed", action="store_true", help="skip failed checks and continue (for check subcommand)")

    # constitution-sync
    subparsers.add_parser("constitution-sync", help="Sync Constitution to Policy Engine")

    # run-phase (Single Entry Point for Phase Execution)
    run_phase_parser = subparsers.add_parser("run-phase", help="Single entry point for Phase execution with pre-flight checks")
    run_phase_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    run_phase_parser.add_argument("--step", type=str, help="Step to execute (e.g. 3.1)")
    run_phase_parser.add_argument("--task", type=str, help="Task description override")
    run_phase_parser.add_argument("--repo", type=str, default=".", help="Repository path")
    run_phase_parser.add_argument("--resume", action="store_true", help="Skip to POST-FLIGHT checks")
    run_phase_parser.add_argument("--no-autoresearch", action="store_true", help="Disable AutoResearch quality check after phase")

    # auto-research command
    autoresearch_parser = subparsers.add_parser("auto-research", help="Execute AutoResearch quality improvement")
    autoresearch_parser.add_argument("--project", "-p", type=str, required=True, help="Project path")
    autoresearch_parser.add_argument("--phase", type=int, default=3, help="Phase number (3-5)")
    autoresearch_parser.add_argument("--iterations", type=int, default=3, help="Max iterations (default: 3)")
    autoresearch_parser.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds (default: 1800 = 30min)")
    autoresearch_parser.add_argument("--dimensions", type=str, default=None, help="Comma-separated dimensions (default: phase preset)")

    # spec-track
    spec_track_parser = subparsers.add_parser("spec-track", help="Spec Tracking")
    spec_track_parser.add_argument("action", choices=["init", "check", "report"],
                                   help="Spec tracking action")

    # resources
    resources_parser = subparsers.add_parser("resources", help="Resource dashboard")
    resources_parser.add_argument("action", choices=["list", "stats", "skills"],
                                 help="Resource action")
    
    # term
    term_parser = subparsers.add_parser("term", help="PM terminology")
    term_parser.add_argument("query", nargs="?", help="Search term")
    
    # pm
    pm_parser = subparsers.add_parser("pm", help="PM Mode")
    pm_parser.add_argument("action", choices=["report", "forecast", "health"],
                         help="PM action")
    
    # eval
    # eval subcommands
    eval_sp = subparsers.add_parser("eval", help="Agent Evaluation")
    eval_sub = eval_sp.add_subparsers(dest="eval_action", help="Evaluation actions")
    
    # eval create
    eval_create = eval_sub.add_parser("create", help="Create evaluation suite")
    eval_create.add_argument("name", help="Suite name")
    eval_create.add_argument("--description", help="Suite description")
    eval_create.add_argument("--iterations", type=int, default=1, help="Iterations")
    
    # eval list
    eval_sub.add_parser("list", help="List evaluation suites")
    
    # eval add
    eval_add = eval_sub.add_parser("add", help="Add test case")
    eval_add.add_argument("suite_id", help="Suite ID")
    eval_add.add_argument("name", nargs="?", help="Test case name")
    eval_add.add_argument("--prompt", help="Test prompt")
    eval_add.add_argument("--expected", help="Expected output")
    
    # eval run
    eval_run = eval_sub.add_parser("run", help="Run evaluation")
    eval_run.add_argument("suite_id", help="Suite ID")
    
    # eval report
    eval_report = eval_sub.add_parser("report", help="Show evaluation report")
    eval_report.add_argument("suite_id", help="Suite ID")
    
    # eval hitl
    eval_sub.add_parser("hitl", help="Human-in-the-Loop status")
    
    # quality
    quality_parser = subparsers.add_parser("quality", help="Data Quality Checker")
    quality_parser.add_argument("action", choices=["check", "report"], default="check")
    
    # enterprise
    enterprise_parser = subparsers.add_parser("enterprise", help="Enterprise Hub")
    enterprise_parser.add_argument("action", choices=["status", "audit"], default="status")
    
    # audit
    audit_parser = subparsers.add_parser("audit", help="AI Audit Logger")
    audit_parser.add_argument("action", choices=["status", "anomalies", "report"], default="status")
    audit_parser.add_argument("--agent", help="Filter by agent ID")
    audit_parser.add_argument("--severity", choices=["low", "medium", "high", "critical"],
                              help="Filter by severity")
    
    # migrate
    migrate_parser = subparsers.add_parser("migrate", help="Framework Migration (CrewAI ↔ LangGraph)")
    migrate_parser.add_argument("file", help="File to migrate")
    migrate_parser.add_argument("--from", dest="from_", choices=["crewai", "langgraph"],
                                help="Source framework")
    migrate_parser.add_argument("--to", choices=["crewai", "langgraph"],
                                help="Target framework")

    # parse
    parse_parser = subparsers.add_parser("parse", help="Structured Output Engine")
    parse_parser.add_argument("--schema", help="Schema name")
    
    # wizard
    wizard_parser = subparsers.add_parser("wizard", help="Setup Wizard")

    # onboarding (P3-3: Interactive Onboarding Wizard)
    onboarding_parser = subparsers.add_parser("onboarding", help="Interactive Onboarding Wizard - Phase-gate workflow guide")
    onboarding_parser.add_argument("--project", "-p", default=".", help="Project directory (default: current directory)")
    onboarding_parser.add_argument("--phase", type=int, default=1, help="Phase to onboard (1, 2, or 3)")
    onboarding_parser.add_argument("--resume", action="store_true", help="Resume from last interrupted checkpoint")
    onboarding_parser.add_argument("--list-phases", action="store_true", help="List available phases and exit")

    # guardrails
    guardrails_parser = subparsers.add_parser("guardrails", help="Security Guardrails")
    guardrails_parser.add_argument("action", choices=["check"], default="check")
    guardrails_parser.add_argument("--text", help="Text to check")

    # security - Deep Security Defense
    security_parser = subparsers.add_parser("security", help="Deep Security Defense Architecture")
    security_parser.add_argument(
        "action",
        choices=["deep-check", "enable-deep-defense", "audit-log", "validate"],
        default="deep-check"
    )
    security_parser.add_argument("--text", help="Text to validate (for validate action)")

    # scale
    scale_parser = subparsers.add_parser("scale", help="AutoScaler")
    scale_parser.add_argument("action", choices=["status", "scale"], default="status")
    scale_parser.add_argument("--current", help="Current instances")
    
    # debug
    debug_parser = subparsers.add_parser("debug", help="Agent Debugger")
    debug_parser.add_argument("action", choices=["status", "stats", "list", "clear", "enable", "disable"],
                            help="Debug action")
    debug_parser.add_argument("--agent-id", help="Agent ID")
    debug_parser.add_argument("--limit", type=int, help="Limit events")
    
    # trace
    trace_parser = subparsers.add_parser("trace", help="Traceability Matrix / Agent Trace")
    trace_parser.add_argument("action", choices=["view", "correlation", "export", "impact", "graphviz", "risk-report", "init", "update", "report", "check"],
                            help="Trace action")
    trace_parser.add_argument("--agent-id", help="Agent ID (for view/correlation/export)")
    trace_parser.add_argument("--correlation", help="Correlation ID")
    trace_parser.add_argument("--limit", type=int, help="Limit events")
    trace_parser.add_argument("--output", "-o", help="Output file")
    trace_parser.add_argument("--file", "-f", help="File path for impact analysis")
    trace_parser.add_argument("--id", help="Requirement ID (for update)")
    trace_parser.add_argument("--status", help="Status: pending|in-progress|completed (for update)")

    # trace-check
    trace_check_parser = subparsers.add_parser("trace-check", help="traceability check (SAD->code / FR->test)")
    trace_check_parser.add_argument("--from", dest="from_phase", required=True,
                                   help="Source phase (phase1, phase2)")
    trace_check_parser.add_argument("--to", dest="to_phase", required=True,
                                   help="Target: phase3 (SAD->code) or phase3-tests (FR->test)")
    trace_check_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # approval
    approval_parser = subparsers.add_parser("approval", help="Human Approval Management")
    approval_parser.add_argument("action", choices=["list", "create", "approve", "reject", "show", "report", "stats"],
                                help="Approval action")
    approval_parser.add_argument("--request-id", help="Request ID")
    approval_parser.add_argument("--name", help="Request name")
    approval_parser.add_argument("--description", help="Request description")
    approval_parser.add_argument("--requester", help="Requester ID")
    approval_parser.add_argument("--requester-name", help="Requester name")
    approval_parser.add_argument("--approval-type", help="Approval type (code_review, deployment, budget)")
    approval_parser.add_argument("--approver", help="Approver ID")
    approval_parser.add_argument("--level", help="Approval level (l1, l2, l3, l4, final)")
    approval_parser.add_argument("--comment", help="Comment")

    # risk
    risk_parser = subparsers.add_parser("risk", help="Risk Registry Management")
    risk_parser.add_argument("action", choices=["add", "list", "show", "close", "mitigate", "report", "stats", "delete"],
                             help="Risk action")
    risk_parser.add_argument("--title", help="Risk title")
    risk_parser.add_argument("--desc", help="Risk description")
    risk_parser.add_argument("--level", help="Risk level (low, medium, high, critical)")
    risk_parser.add_argument("--owner", help="Risk owner")
    risk_parser.add_argument("--impact", help="Impact description")
    risk_parser.add_argument("--probability", type=float, help="Probability (0-1)")
    risk_parser.add_argument("--mitigation", help="Mitigation strategy")
    risk_parser.add_argument("--risk-id", help="Risk ID")
    risk_parser.add_argument("--status", help="Filter by status (open, mitigated, accepted, closed)")

    # roi (ROI Dashboard)
    roi_parser = subparsers.add_parser("roi", help="ROI Tracking Dashboard")
    roi_parser.add_argument("subcommand", nargs="?", default="dashboard",
                           choices=["dashboard", "report"],
                           help="ROI subcommand")
    roi_parser.add_argument("period", nargs="?", choices=["day", "week", "month"], default="month",
                           help="Time period for report")

    # p2p
    p2p_parser = subparsers.add_parser("p2p", help="P2P Team Config")
    p2p_sub = p2p_parser.add_subparsers(dest="p2p_action", help="P2P actions")
    p2p_init = p2p_sub.add_parser("init", help="Initialize team from JSON")
    p2p_init.add_argument("config_path", help="Path to team config JSON file")
    p2p_sub.add_parser("status", help="Show team status")
    p2p_sub.add_parser("list", help="List team members")

    # hitl
    hitl_parser = subparsers.add_parser("hitl", help="HITL Human-in-the-Loop Management")
    hitl_parser.add_argument("action", choices=["register", "list", "assign", "approve", "reject", "show", "stats", "report", "escalate"],
                            help="HITL action")
    hitl_parser.add_argument("owner_id", nargs="?", help="Owner ID or Output ID depending on action")
    hitl_parser.add_argument("--name", help="Owner name")
    hitl_parser.add_argument("--email", help="Owner email")
    hitl_parser.add_argument("--role", help="Owner role")
    hitl_parser.add_argument("--agent-id", dest="agent_id", help="Agent ID for assignment")
    hitl_parser.add_argument("--output-id", dest="output_id", help="Output ID")
    hitl_parser.add_argument("--approver", help="Approver ID")
    hitl_parser.add_argument("--feedback", help="Feedback/reason")
    hitl_parser.add_argument("--reason", help="Escalation reason")
    hitl_parser.add_argument("--level", help="Escalation level (owner, manager, executive)")
    hitl_parser.add_argument("--list-type", dest="list_type", choices=["owners", "outputs", "pending"], default="owners",
                           help="List type (for 'list' action)")
    hitl_parser.add_argument("--status-filter", dest="status_filter", 
                           choices=["draft", "pending_review", "approved", "revision_requested", "completed", "escalated"],
                           help="Filter outputs by status")
    hitl_parser.add_argument("--owner-filter", dest="owner_filter", help="Filter by owner ID")

    # version
    subparsers.add_parser("version", help="Show version")
    
    # integrity (HR-14: Integrity score calculation)
    integrity_parser = subparsers.add_parser("integrity", help="HR-14 Integrity score calculation")
    integrity_parser.add_argument("--project", default=".", dest="project",
                                  help="project root directory (default: .)")

    # gatekeeper
    gatekeeper_parser = subparsers.add_parser("gatekeeper", help="Workflow Gatekeeper")
    gatekeeper_parser.add_argument("action", choices=["status", "check", "enforce"],
                                   help="Gatekeeper action")
    
    # confirmations (double confirmation)
    confirmations_parser = subparsers.add_parser("confirmations", help="Double Confirmation Management")
    confirmations_parser.add_argument("action", choices=["list", "confirm", "reject", "status", "check"],
                                      help="Confirmation action")
    confirmations_parser.add_argument("--id", dest="confirmation_id", help="Confirmation ID")
    confirmations_parser.add_argument("--by", dest="confirmed_by", help="Confirmed by (agent or human)")
    confirmations_parser.add_argument("--reason", help="Rejection reason")
    confirmations_parser.add_argument("--operation", help="Filter by operation type")
    
    # release (with double confirmation)
    release_parser = subparsers.add_parser("release", help="Release Management (requires confirmation)")
    release_parser.add_argument("--version", help="Version to release")
    release_parser.add_argument("--repo", help="Repository name")
    release_parser.add_argument("--confirm", action="store_true", help="Request confirmation before release")
    
    # policy (policy engine)
    subparsers.add_parser("policy", help="Run Policy Engine checks")
    
    # install-hook (pre-commit hook installer)
    install_hook_parser = subparsers.add_parser("install-hook", help="Install pre-commit hook")
    install_hook_parser.add_argument("--force", action="store_true", help="Overwrite existing hooks")
    
    # enforcement-config (Unified Enforcement Configuration)
    enforcement_config_parser = subparsers.add_parser("enforcement-config", help="Unified Enforcement Configuration")
    enforcement_config_parser.add_argument("action", nargs="?", choices=["init", "set", "show", "detect"],
                                          help="Action: init, set, show, detect")
    enforcement_config_parser.add_argument("mode", nargs="?", choices=["local", "github", "gitlab", "jenkins", "azure"],
                                          help="Mode for 'set' action")

    # enforcement (Unified Enforcement CLI)
    enforcement_parser = subparsers.add_parser("enforcement", help="Unified Enforcement CLI")
    enforcement_parser.add_argument("subcommand", nargs="?", default="run",
                                   choices=["run", "check", "status", "install", "config"],
                                   help="Enforcement subcommand")
    enforcement_parser.add_argument("mode", nargs="?", choices=["local", "github", "gitlab", "jenkins", "azure"],
                                   help="Mode for 'config' subcommand")

    # quality-gate (Quality Gate - quality gate check)
    quality_gate_parser = subparsers.add_parser("quality-gate", aliases=["qg"], help="Quality Gate - quality gate check")
    quality_gate_parser.add_argument("subcommand", nargs="?", default="check",
                                     choices=["check", "all", "doc", "docs", "phase", "aspice", "ai-test"],
                                     help="Quality gate subcommand")
    quality_gate_parser.add_argument("--project", default=".", help="Project root path")
    quality_gate_parser.add_argument("--phase", type=int, default=4, help="Phase number (default: 4 - Testing)")
    quality_gate_parser.add_argument("--target", "-t", help="Target source file or directory for AI test generation")
    quality_gate_parser.add_argument("--output", "-o", default="tests/ai_generated", help="Output directory")
    quality_gate_parser.add_argument("--model", "-m", help="LLM model override")
    quality_gate_parser.add_argument("--context", "-c", nargs="*", help="Context files (SRS.md, SAD.md)")

    # enforce (Framework Enforcement)
    enforce_parser = subparsers.add_parser("enforce", help="Framework Enforcement - unified execution of all enforcement")
    enforce_parser.add_argument("--level", choices=["BLOCK", "WARN", "ALL"], default="ALL",
                               help="Enforcement level")
    enforce_parser.add_argument("--spec", action="store_true", help="Only spec tracking")
    enforce_parser.add_argument("--constitution", action="store_true", help="Only constitution")
    enforce_parser.add_argument("--project", default=".", help="Project root path")
    enforce_parser.add_argument("--aspice-report", action="store_true",
                              help="Generate ASPICE traceability report")
    enforce_parser.add_argument("--auto-fix", action="store_true", help="auto-fix known issues")
    enforce_parser.add_argument("--skip-failed", action="store_true", help="skip failed checks and continue")

    # decision (Decision Gate - decision classification gate)
    decision_parser = subparsers.add_parser("decision", aliases=["dec"], help="Decision Gate - decision classification gate")
    decision_parser.add_argument("subcommand", nargs="?", default="list",
                                choices=["classify", "add", "list", "ls", "pending", "p", "confirm", "report"],
                                help="Decision gate subcommand")
    decision_parser.add_argument("args", nargs="*", help="Arguments for subcommand")

    # agent-proof-hook (Agent-Proof Hook Management)
    agent_proof_hook_parser = subparsers.add_parser("agent-proof-hook", help="Agent-Proof Hook Management")
    agent_proof_hook_parser.add_argument("action", nargs="?", choices=["install", "verify", "uninstall"],
                                         help="Action: install, verify, uninstall")

    # memory (Memory Governance Framework)
    memory_parser = subparsers.add_parser("memory", help="Memory Governance Framework")
    memory_parser.add_argument("subcommand", nargs="?", default="status",
                              choices=["validate", "status", "audit", "resolve"],
                              help="Memory governance subcommand")

    # m27 (M2.7 Self-Evolving Integration)
    m27_parser = subparsers.add_parser("m27", help="M2.7 Self-Evolving Integration")
    m27_parser.add_argument("sub", nargs="?", default="status",
                           choices=["status", "analyze", "iterate", "optimize"],
                           help="M2.7 action")
    m27_parser.add_argument("--log", dest="failure_log", help="Failure log for analysis")

    # metrics (Code Metrics)
    metrics_parser = subparsers.add_parser("metrics", help="Code Metrics - code quality metrics")
    metrics_parser.add_argument("subcommand", nargs="?", default="report",
                              choices=["report", "check", "history"],
                              help="Metrics subcommand")

    # debt (Technical Debt Registry - technical debt tracking)
    debt_parser = subparsers.add_parser("debt", help="Technical Debt Registry - technical debt tracking")
    debt_parser.add_argument("subcommand", nargs="?", default="list",
                            choices=["add", "list", "ls", "open", "resolve", "accept", "report"],
                            help="Debt subcommand")
    debt_parser.add_argument("args", nargs="*", help="Arguments for subcommand")
    debt_parser.add_argument("--severity", dest="severity", help="Severity level (high|medium|low)")
    debt_parser.add_argument("--ticket", dest="ticket", help="Associated ticket ID")

    # adr (Architecture Decision Records)
    adr_parser = subparsers.add_parser("adr", help="Architecture Decision Records - architecture decision records")
    adr_parser.add_argument("adr_action", nargs="?", default="list",
                           choices=["create", "new", "list", "ls", "get", "show", "status", "export", "report"],
                           help="ADR action")
    adr_parser.add_argument("args", nargs="*", help="Arguments for ADR subcommand")

    # persona
    persona_parser = subparsers.add_parser("persona", help="Agent Persona")
    persona_parser.add_argument("action", choices=["list", "apply", "show"],
                              help="Persona action")
    persona_parser.add_argument("persona_type", nargs="?",
                              help="Persona type (developer, architect, qa, pm, devops, reviewer)")

    # ralph (Ralph Mode - long-running task monitoring)
    ralph_parser = subparsers.add_parser("ralph", help="Ralph Mode - long-running task monitoring")
    ralph_parser.add_argument(
        "ralph_command",
        nargs="+",
        help="Ralph subcommands: start, status, stop, list, init, advance"
    )

    # stage-pass (STAGE_PASS Generator - integrated quality certification)
    stage_pass_parser = subparsers.add_parser("stage-pass", help="STAGE_PASS Generator - integrated quality certification")
    stage_pass_parser.add_argument("--phase", type=int, required=True, choices=range(1, 9),
                                   help="Phase number (1-8)")
    stage_pass_parser.add_argument("--project", default=".", help="Project root path")
    stage_pass_parser.add_argument("--auto-fix", action="store_true", help="auto-fix known issues")
    stage_pass_parser.add_argument("--skip-failed", action="store_true", help="skip failed checks and continue")

    # phase-verify (Phase truth verification)
    phase_verify_parser = subparsers.add_parser("phase-verify", help="Phase truth verification")
    phase_verify_parser.add_argument("--phase", type=int, required=True, choices=range(1, 9),
                                     help="Phase number (1-8)")
    phase_verify_parser.add_argument("--project", default=".", help="Project root path")

    # skill-check (SKILL.md mandatory read check)
    skill_check_parser = subparsers.add_parser("skill-check", help="SKILL.md mandatory read check")
    skill_check_parser.add_argument("--phase", type=int, default=1, choices=range(1, 9),
                                   help="Phase number (1-8)")
    skill_check_parser.add_argument("--mode", choices=["preheat", "interrogate", "citation"],
                                   help="check mode: preheat=preheat, interrogate=interrogate, citation=citation")

    # model-recommend (Phase -> Model recommendation)
    model_recommend_parser = subparsers.add_parser("model-recommend", help="Phase -> Model recommendation")
    model_recommend_parser.add_argument("--phase", type=int, help="Phase number (1-8), reads from state.json if not specified")
    model_recommend_parser.add_argument("--repo", default=".", help="Repo path (default: .)")
    model_recommend_parser.add_argument("--provider", action="store_true", help="Show Provider details (JSON)")
    model_recommend_parser.add_argument("--task-hint", help="Task hint (simple/coding/review)")

    # context-compress (Context Compression - three-layer compression)
    context_compress_parser = subparsers.add_parser("context-compress", help="Context Compression - three-layer compression")
    context_compress_parser.add_argument("--level", default="auto", choices=["L1", "L2", "L3", "auto"],
                                        help="Compression level (default: auto)")
    context_compress_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # update-project-status (Update PROJECT_STATUS.md)
    update_status_parser = subparsers.add_parser("update-project-status", help="Update PROJECT_STATUS.md")
    update_status_parser.add_argument("--step", required=True, help="Step name (e.g. 4.1)")
    update_status_parser.add_argument("--module", required=True, help="Module name (e.g. SynthEngine)")
    update_status_parser.add_argument("--action", required=True, help="Action description")
    update_status_parser.add_argument("--status", default="in-progress", help="Status (pending/in-progress/completed)")
    update_status_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # update-step (Update Phase/Step/Module tracking)
    update_step_parser = subparsers.add_parser("update-step", help="Update Phase/Step/Module tracking")
    update_step_parser.add_argument("--step", required=True, help="Step name (e.g. 3.1)")
    update_step_parser.add_argument("--module", required=True, help="Module name (e.g. LexiconMapper)")
    update_step_parser.add_argument("--action", help="Next action description")
    update_step_parser.add_argument("--phase", type=int, choices=range(1, 9), help="Phase number (for time tracking initialization)")
    update_step_parser.add_argument("--estimated-minutes", type=int, help="Estimated Phase duration (minutes)")
    update_step_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # end-phase (End current Phase)
    end_phase_parser = subparsers.add_parser("end-phase", help="End current Phase")
    end_phase_parser.add_argument("--phase", type=int, help="Phase number (1-8)")
    end_phase_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # update-artifact (Update artifact version)
    update_artifact_parser = subparsers.add_parser("update-artifact", help="Update artifact version in state.json")
    update_artifact_parser.add_argument("--name", required=True, help="Artifact name (e.g. SRS.md, SAD.md)")
    update_artifact_parser.add_argument("--version", required=True, help="Version (e.g. v1.0.0)")
    update_artifact_parser.add_argument("--path", required=True, help="Artifact path")
    update_artifact_parser.add_argument("--summary", help="Summary within 50 characters")
    update_artifact_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # add-task (Add task to task graph)
    add_task_parser = subparsers.add_parser("add-task", help="Add task to task graph")
    add_task_parser.add_argument("--task-id", required=True, help="Task ID")
    add_task_parser.add_argument("--title", required=True, help="Task title")
    add_task_parser.add_argument("--dependencies", help="Dependency task IDs (comma-separated)")
    add_task_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # task-result (Update task result)
    task_result_parser = subparsers.add_parser("task-result", help="Update task result")
    task_result_parser.add_argument("--task-id", required=True, help="Task ID")
    task_result_parser.add_argument("--result", required=True, help="Task result")
    task_result_parser.add_argument("--summary", required=True, help="Task summary")
    task_result_parser.add_argument("--status", default="completed", help="Task status (pending/in-progress/completed)")
    task_result_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # verify-artifact (Verify_Agent - independently verify artifact)
    verify_artifact_parser = subparsers.add_parser("verify-artifact", help="Verify_Agent - independently verify artifact correctness")
    verify_artifact_parser.add_argument("--phase", type=int, default=3, help="Phase number (default: 3)")
    verify_artifact_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # retry-test
    subparsers.add_parser("retry-test", help="Test RetryHandler with dynamic prompt adjustment")

    # fsm-status (FSM state query)
    fsm_status_parser = subparsers.add_parser("fsm-status", help="FSM state query")
    fsm_status_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # fsm-transition (FSM manual state transition)
    fsm_transition_parser = subparsers.add_parser("fsm-transition", help="FSM manual state transition")
    fsm_transition_parser.add_argument("--to", required=True,
                                       help="Target state (INIT/RUNNING/VERIFYING/WRITING/PAUSED/FREEZE/COMPLETED)")
    fsm_transition_parser.add_argument("--reason", help="Transition reason")
    fsm_transition_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # fsm-resume (FSM release brake)
    fsm_resume_parser = subparsers.add_parser("fsm-resume", help="FSM release brake (PAUSED -> RUNNING)")
    fsm_resume_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # fsm-unfreeze (FSM unfreeze)
    fsm_unfreeze_parser = subparsers.add_parser("fsm-unfreeze", help="FSM unfreeze (FREEZE -> INIT)")
    fsm_unfreeze_parser.add_argument("--repo", default=".", help="Repo path (default: .)")

    # session-save (Save complete session state)
    session_save_parser = subparsers.add_parser("session-save", help="Save complete session state")
    session_save_parser.add_argument("--id", required=True, help="Session ID")
    session_save_parser.add_argument("--state", help="State file path (default: .methodology/session_state.json)")

    # session-load (Restore complete session state)
    session_load_parser = subparsers.add_parser("session-load", help="Restore complete session state")
    session_load_parser.add_argument("--id", required=True, help="Session ID")
    session_load_parser.add_argument("--output", help="Output file (default: .methodology/session_state_loaded.json)")

    # session-list (List all saved sessions)
    subparsers.add_parser("session-list", help="List all saved sessions")

    # session-delete (Delete specified session)
    session_delete_parser = subparsers.add_parser("session-delete", help="Delete specified session")
    session_delete_parser.add_argument("--id", required=True, help="Session ID")

    # tool-registry (Tool Registry - Clawd-Code TOOL_HANDLERS style)
    tr_parser = subparsers.add_parser("tool-registry", help="Tool Registry - Clawd-Code TOOL_HANDLERS tool management")
    tr_parser.add_argument("--action", "-a", dest="action",
                          choices=["list", "register", "unregister", "get", "dispatch"],
                          help="Operation: list/register/unregister/get/dispatch")
    tr_parser.add_argument("--list", action="store_true", help="List all registered tools")
    tr_parser.add_argument("--register", metavar="TOOL_NAME", help="Register tool name")
    tr_parser.add_argument("--handler", metavar="HANDLER_PATH", help="Handler path (e.g. module.func)")
    tr_parser.add_argument("--handler-path", metavar="HANDLER_PATH", dest="handler_path", help="Handler path (alias for --handler)")
    tr_parser.add_argument("--unregister", metavar="TOOL_NAME", help="Unregister tool name")
    tr_parser.add_argument("--get", metavar="TOOL_NAME", help="Query tool handler")
    tr_parser.add_argument("--dispatch", metavar="TOOL_NAME", help="Dispatch call to tool")
    tr_parser.add_argument("--tool-name", dest="tool_name", metavar="TOOL_NAME", help="Tool name")
    tr_parser.add_argument("--kwargs", nargs="*", metavar="KEY=VALUE", help="dispatch parameters (e.g. path=/tmp/test.txt)")

    # plan-phase (Phase Planner - automated execution plan generation)
    plan_phase_parser = subparsers.add_parser("plan-phase", help="Phase Planner - automated execution plan generation")
    plan_phase_parser.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    plan_phase_parser.add_argument("--goal", type=str, default="", help="Goal description for this phase")
    plan_phase_parser.add_argument("--repair", action="store_true", help="Repair mode: generate repair plan for failed steps")
    plan_phase_parser.add_argument("--step", type=str, help="Specify Step (e.g. 3.2), shows Step iteration history in --history mode")
    plan_phase_parser.add_argument("--history", action="store_true", help="Show Phase iteration history")
    plan_phase_parser.add_argument("--with-timeline", action="store_true", help="Show full timeline estimate")
    plan_phase_parser.add_argument("--repo", default=".", dest="repo", help="Project root directory (default: .)")
    plan_phase_parser.add_argument("--detailed", action="store_true", help="Generate complete FR detailed tasks (requires SRS.md parsing)")

    # steering
    steering_parser = subparsers.add_parser("steering", help="Steering Loop - AB Workflow direction control")
    steering_sub = steering_parser.add_subparsers(dest="steering_action", help="Steering actions")

    # steering run
    steering_run = steering_sub.add_parser("run", help="Run Steering Loop guidance")
    steering_run.add_argument("--project", "-p", default=".", help="Project path")
    steering_run.add_argument("--phase", type=int, required=True, help="Phase number (1-8)")
    steering_run.add_argument("--max-rounds", type=int, default=None, help="Maximum iteration rounds (default: config.max_iterations)")

    # steering status
    steering_sub.add_parser("status", help="Show current Steering status")

    # steering history
    steering_sub.add_parser("history", help="Show Steering guidance history")

    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    cli = MethodologyCLI()
    return cli.run(args)


if __name__ == "__main__":
    sys.exit(main())

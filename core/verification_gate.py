#!/usr/bin/env python3
"""
Verification Gates - Verification Gates
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Any
from datetime import datetime


class GateStatus(Enum):
    NOT_REACHED = "not_reached"
    PASSED = "passed"
    FAILED = "failed"
    BYPASSED = "bypassed"


class Gate:
    """Single verification gate"""

    def __init__(self, name: str, required_output: str = None,
                 validator: Callable = None, auto_pass: bool = False):
        self.name = name
        self.required_output = required_output
        self.validator = validator
        self.auto_pass = auto_pass
        self.status = GateStatus.NOT_REACHED
        self.verified_at: Optional[datetime] = None
        self.evidence: Optional[Any] = None

    def check(self, context: dict) -> bool:
        if self.auto_pass:
            self.status = GateStatus.PASSED
            self.verified_at = datetime.now()
            self.evidence = {"auto_pass": True}
            return True
        if self.validator:
            try:
                result = self.validator(context)
                self.status = GateStatus.PASSED if result else GateStatus.FAILED
                self.verified_at = datetime.now()
                self.evidence = {"validator_result": result}
                return result
            except Exception as e:
                self.status = GateStatus.FAILED
                self.verified_at = datetime.now()
                self.evidence = {"error": str(e)}
                return False
        if self.required_output:
            if self.required_output in context:
                self.status = GateStatus.PASSED
                self.verified_at = datetime.now()
                self.evidence = {"output_found": self.required_output}
                return True
            self.status = GateStatus.NOT_REACHED
            return False
        return False

    def bypass(self, reason: str = None):
        self.status = GateStatus.BYPASSED
        self.verified_at = datetime.now()
        self.evidence = {"bypass_reason": reason or "manual_bypass"}

    def reset(self):
        self.status = GateStatus.NOT_REACHED
        self.verified_at = None
        self.evidence = None


class VerificationGates:
    """Verification gate manager"""

    DEFAULT_GATES = {
        "task_created": {"name": "Task Created", "required_output": "task_spec", "auto_pass": False},
        "agent_assigned": {"name": "Agent Assigned", "required_output": "assignment", "auto_pass": False},
        "output_generated": {"name": "Output Generated", "required_output": "result", "auto_pass": False},
        "quality_check": {"name": "Quality Check", "auto_pass": False},
        "human_approved": {"name": "Human Approved", "required_output": "approval", "auto_pass": False},
        "completed": {"name": "Task Completed", "required_output": "final_result", "auto_pass": False},
    }

    def __init__(self):
        self.gates: Dict[str, Gate] = {}
        self.gate_sequence: List[str] = []

    def register_gate(self, gate_id: str, gate: Gate):
        self.gates[gate_id] = gate

    def register_default_gates(self, gate_ids: List[str] = None):
        if gate_ids is None:
            gate_ids = list(self.DEFAULT_GATES.keys())
        for gate_id in gate_ids:
            if gate_id in self.DEFAULT_GATES:
                config = self.DEFAULT_GATES[gate_id]
                self.register_gate(gate_id, Gate(
                    name=config["name"],
                    required_output=config.get("required_output"),
                    auto_pass=config.get("auto_pass", False)
                ))

    def execute_sequence(self, context: dict) -> dict:
        return {gid: self.gates[gid].check(context)
                for gid in self.gate_sequence if gid in self.gates}

    def check_gate(self, gate_id: str, context: dict) -> bool:
        gate = self.gates.get(gate_id)
        return gate.check(context) if gate else False

    def get_status(self) -> dict:
        return {gid: {"name": g.name, "status": g.status.value,
                      "verified_at": g.verified_at.isoformat() if g.verified_at else None,
                      "evidence": g.evidence}
                for gid, g in self.gates.items()}

    def get_passed_count(self) -> int:
        return sum(1 for g in self.gates.values() if g.status == GateStatus.PASSED)

    def get_failed_count(self) -> int:
        return sum(1 for g in self.gates.values() if g.status == GateStatus.FAILED)

    def reset_all(self):
        for gate in self.gates.values():
            gate.reset()


class HITLGates(VerificationGates):
    def __init__(self):
        super().__init__()
        self.gate_sequence = ["task_created", "output_generated", "human_approved", "completed"]
        self.register_default_gates(self.gate_sequence)


class AutonomousGates(VerificationGates):
    def __init__(self):
        super().__init__()
        self.gate_sequence = ["task_created", "agent_assigned", "output_generated",
                               "quality_check", "completed"]
        self.register_default_gates(self.gate_sequence)

#!/usr/bin/env python3
"""
Constitution Policy Sync
========================
Auto-generates PolicyEngine policies from Constitution.md.

Purpose:
- Eliminate hard-coded policies
- Keep Constitution and PolicyEngine in sync
- Single source of truth: Constitution defines, PolicyEngine enforces
"""

import re
import os
from typing import List, Dict, Any, Optional, Callable

from enforcement.policy_engine import PolicyEngine, Policy, EnforcementLevel


class ConstitutionPolicyGenerator:
    """
    Auto-generate Policies from Constitution.md.

    Usage::

        generator = ConstitutionPolicyGenerator()

        # Generate policy list
        policies = generator.generate("CONSTITUTION.md")

        # Or sync directly to a PolicyEngine
        engine = generator.sync_to_engine()
    """

    DEFAULT_CONSTITUTION_PATHS = [
        "CONSTITUTION.md",
        "constitution/CONSTITUTION.md",
        ".methodology/CONSTITUTION.md",
    ]

    def __init__(self):
        self.rules: List[Dict[str, Any]] = []

    def find_constitution(self) -> Optional[str]:
        """Locate Constitution.md."""
        for path in self.DEFAULT_CONSTITUTION_PATHS:
            if os.path.exists(path):
                return path
        return None

    def parse_constitution(self, path: str) -> List[Dict[str, Any]]:
        """
        Parse rules from Constitution.md.

        Supported formats::

            ## Rule: commit-has-task-id
            [SEVERITY: critical]
            [THRESHOLD: 90]
            All commits must contain a task_id, format: [TASK-XXX]

            ### R001: Quality Gate
            **Severity**: critical
            **Threshold**: 90
            Description...
        """
        rules = []
        with open(path, "r") as f:
            content = f.read()

        current_rule = None
        for line in content.split("\n"):
            match = re.match(r"## Rule: (\w+)", line)
            if match:
                if current_rule:
                    rules.append(current_rule)
                current_rule = {
                    "id": match.group(1),
                    "description": "",
                    "severity": "medium",
                    "threshold": None,
                    "check_type": "commit_message",
                }
                continue

            match = re.match(r"### (R\d+): (.+)", line)
            if match and not current_rule:
                current_rule = {
                    "id": match.group(1),
                    "description": match.group(2),
                    "severity": "medium",
                    "threshold": None,
                    "check_type": "general",
                }
                continue

            if current_rule:
                m = re.match(r"\[SEVERITY: (\w+)\]", line, re.IGNORECASE)
                if m:
                    current_rule["severity"] = m.group(1).lower()
                    continue
                m = re.match(r"\[THRESHOLD: ([\d.]+)\]", line)
                if m:
                    current_rule["threshold"] = float(m.group(1))
                    continue
                m = re.match(r"\*\*Severity\*\*:\s*(\w+)", line, re.IGNORECASE)
                if m:
                    current_rule["severity"] = m.group(1).lower()
                    continue
                m = re.match(r"\*\*Threshold\*\*:\s*([\d.]+)", line)
                if m:
                    current_rule["threshold"] = float(m.group(1))
                    continue
                if line.strip() and not line.startswith("#") and not line.startswith("["):
                    current_rule["description"] += " " + line.strip()

        if current_rule:
            rules.append(current_rule)
        self.rules = rules
        return rules

    def create_check_fn(self, rule: Dict[str, Any]) -> Callable:
        """Create a check function appropriate for the rule type."""
        check_type = rule.get("check_type", "general")
        threshold = rule.get("threshold")

        if check_type == "commit_message":
            def check_fn():
                commit_file = os.environ.get("COMMIT_MSG_FILE", ".git/COMMIT_EDITMSG")
                if os.path.exists(commit_file):
                    with open(commit_file, "r") as f:
                        msg = f.read()
                    return bool(re.search(r"\[[A-Z]+-\d+\]", msg))
                return True
            return check_fn

        if check_type == "quality_gate":
            def check_fn():
                score_file = ".methodology/.quality_score"
                if os.path.exists(score_file):
                    with open(score_file, "r") as f:
                        return float(f.read().strip()) >= (threshold or 90)
                return True
            return check_fn

        if check_type == "coverage":
            def check_fn():
                coverage_file = ".methodology/.coverage"
                if os.path.exists(coverage_file):
                    with open(coverage_file, "r") as f:
                        return float(f.read().strip()) >= (threshold or 80)
                return True
            return check_fn

        if check_type == "security":
            def check_fn():
                score_file = ".methodology/.security_score"
                if os.path.exists(score_file):
                    with open(score_file, "r") as f:
                        return float(f.read().strip()) >= (threshold or 95)
                return True
            return check_fn

        return lambda: True  # Generic: always pass

    def generate(self, constitution_path: str = None) -> List[Policy]:
        """Generate a list of Policy objects from Constitution.md."""
        if constitution_path is None:
            constitution_path = self.find_constitution()
        if constitution_path is None:
            print("Warning: Constitution.md not found, using default policies")
            return []
        rules = self.parse_constitution(constitution_path)
        return [
            Policy(
                id=rule["id"],
                description=rule["description"].strip(),
                check_fn=self.create_check_fn(rule),
                enforcement=EnforcementLevel.BLOCK if rule["severity"] == "critical" else EnforcementLevel.WARN,
                severity=rule["severity"],
                metadata={"threshold": rule.get("threshold"), "check_type": rule.get("check_type")},
            )
            for rule in rules
        ]

    def sync_to_engine(self, engine: PolicyEngine = None) -> PolicyEngine:
        """Sync generated policies into a PolicyEngine instance."""
        policies = self.generate()
        if engine is None:
            engine = PolicyEngine()
        engine.policies.clear()
        for policy in policies:
            engine.policies.append(policy)
        return engine

    def sync(self, output_path: str = ".methodology/policies_generated.py"):
        """Sync and persist policies as a Python file for hook import."""
        policies = self.generate()
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write("# Auto-generated from Constitution.md\n")
            f.write("# Do not edit manually\n\nPOLICIES = [\n")
            for p in policies:
                f.write(f"    # {p.id}: {p.description}\n")
                f.write(f"    # severity: {p.severity}, enforcement: {p.enforcement.value}\n\n")
            f.write("]\n")
        print(f"Synced {len(policies)} policies to {output_path}")
        return policies


def main():
    """CLI entry point."""
    import sys
    generator = ConstitutionPolicyGenerator()
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "sync":
            generator.sync()
        elif cmd == "generate":
            policies = generator.generate()
            print(f"Generated {len(policies)} policies:")
            for p in policies:
                print(f"  - {p.id}: {p.description[:60]}... [{p.severity}]")
        elif cmd == "preview":
            path = generator.find_constitution()
            if path:
                rules = generator.parse_constitution(path)
                print(f"Found {len(rules)} rules in {path}:")
                for r in rules:
                    print(f"  - {r['id']}: {r.get('description', '')[:60]}... [{r['severity']}]")
            else:
                print("Constitution.md not found")
    else:
        generator.sync()


if __name__ == "__main__":
    main()

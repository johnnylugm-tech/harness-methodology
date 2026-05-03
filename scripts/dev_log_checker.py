#!/usr/bin/env python3
"""
Development Log Checker
=======================
Validates DEVELOPMENT_LOG.md format against spec.

Usage:
    from scripts.dev_log_checker import DevLogChecker

    checker = DevLogChecker("/path/to/project")
    result = checker.check()
    print(result.passed)

    # Or via command line:
    # python -m scripts.dev_log_checker /path/to/project
"""

import re
import sys
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Any, Optional


@dataclass
class DecisionGateRecord:
    """Decision Gate record"""
    decision: str
    session_id: Optional[str]
    confirmed: bool
    date: Optional[str]
    line_number: int


@dataclass
class CommandRecord:
    """Command execution record"""
    command: str
    output_present: bool  # whether actual output is present
    result: Optional[str]  # execution result
    line_number: int


@dataclass
class SessionIdRecord:
    """Session ID record"""
    agent_role: str
    session_id: str
    line_number: int


@dataclass
class DevLogCheckResult:
    """DEVELOPMENT_LOG check result"""
    passed: bool
    file_exists: bool
    has_header: bool
    has_phase_records: bool
    decision_gates: List[DecisionGateRecord]
    session_ids: List[SessionIdRecord]
    commands: List[CommandRecord]
    errors: List[str]
    warnings: List[str]
    details: Dict[str, Any]

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def print_summary(self) -> str:
        """Generate summary report"""
        status = "PASS" if self.passed else "FAIL"

        lines = [
            f"{'='*60}",
            "Development Log Checker Result",
            f"{'='*60}",
            f"Status: {status}",
            f"File Exists: {self.file_exists}",
            f"Has Header: {self.has_header}",
            f"Has Phase Records: {self.has_phase_records}",
            "",
            f"Decision Gates: {len(self.decision_gates)}",
            f"Session IDs: {len(self.session_ids)}",
            f"Commands with Output: {sum(1 for c in self.commands if c.output_present)}",
            "",
        ]

        if self.errors:
            lines.append("Errors:")
            for error in self.errors:
                lines.append(f"  ERR {error}")
            lines.append("")

        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings:
                lines.append(f"  WARN {warning}")
            lines.append("")

        lines.append(f"{'='*60}")
        return "\n".join(lines)


class DevLogChecker:
    """DEVELOPMENT_LOG format checker"""

    # Required section headings
    REQUIRED_SECTIONS = [
        "Phase",
        "Quality Gate",
    ]

    # session_id regex pattern
    SESSION_ID_PATTERN = re.compile(r'session_id[:\s]+([a-zA-Z0-9_-]+)', re.IGNORECASE)

    # Decision Gate marker
    DECISION_GATE_PATTERN = re.compile(
        r'(?:Decision Gate|DECISION GATE)[:\s]*(.+?)(?:\n|$)',
        re.IGNORECASE
    )

    # Command execution regex
    COMMAND_PATTERN = re.compile(
        r'(?:Command|Execute)[:\s]*`?(.+?)`?',
        re.IGNORECASE
    )

    # Result regex
    RESULT_PATTERN = re.compile(
        r'(?:Result)[:\s]*(.+?)(?:\n\n|\n##|\Z)',
        re.IGNORECASE
    )

    # Pass/fail status check
    STATUS_PATTERN = re.compile(
        r'(PASS|FAIL|APPROVE|REJECT)',
        re.IGNORECASE
    )

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.dev_log_path = self.project_path / "DEVELOPMENT_LOG.md"

    def check(self) -> DevLogCheckResult:
        """
        Run DEVELOPMENT_LOG check.

        Returns:
            DevLogCheckResult: check result
        """
        errors = []
        warnings: list[str] = []

        # Check if file exists
        file_exists = self.dev_log_path.exists()

        if not file_exists:
            errors.append("DEVELOPMENT_LOG.md not found")
            return DevLogCheckResult(
                passed=False,
                file_exists=False,
                has_header=False,
                has_phase_records=False,
                decision_gates=[],
                session_ids=[],
                commands=[],
                errors=errors,
                warnings=warnings,
                details={"file_path": str(self.dev_log_path)}
            )

        # Read file content
        try:
            content = self.dev_log_path.read_text(encoding='utf-8')
            lines = content.split('\n')
        except Exception as e:
            errors.append(f"Failed to read file: {e}")
            return DevLogCheckResult(
                passed=False,
                file_exists=True,
                has_header=False,
                has_phase_records=False,
                decision_gates=[],
                session_ids=[],
                commands=[],
                errors=errors,
                warnings=warnings,
                details={"error": str(e)}
            )

        # Check header
        has_header = self._check_header(content)
        if not has_header:
            warnings.append("Missing proper header (e.g., ## Development Log)")

        # Check Phase records
        has_phase_records = self._check_phase_records(content)
        if not has_phase_records:
            warnings.append("No Phase records found")

        # Parse session_ids
        session_ids = self._extract_session_ids(lines)
        if not session_ids:
            warnings.append("No session_id records found (required for Phase 1-8)")

        # Parse Decision Gates
        decision_gates = self._extract_decision_gates(lines)
        if not decision_gates:
            warnings.append("No Decision Gate records found")

        # Parse command execution records
        commands = self._extract_commands(lines, content)

        # Check empty records
        self._check_empty_records(content, errors)

        # Calculate final result
        passed = len(errors) == 0

        return DevLogCheckResult(
            passed=passed,
            file_exists=file_exists,
            has_header=has_header,
            has_phase_records=has_phase_records,
            decision_gates=decision_gates,
            session_ids=session_ids,
            commands=commands,
            errors=errors,
            warnings=warnings,
            details={
                "file_path": str(self.dev_log_path),
                "total_lines": len(lines),
                "content_length": len(content)
            }
        )

    def _check_header(self, content: str) -> bool:
        """Check if there is a proper header"""
        return bool(re.search(r'^##\s+\w+', content, re.MULTILINE))

    def _check_phase_records(self, content: str) -> bool:
        """Check if Phase records exist"""
        return "Phase" in content and "Quality Gate" in content

    def _extract_session_ids(self, lines: List[str]) -> List[SessionIdRecord]:
        """Extract session_id records"""
        session_ids = []

        for i, line in enumerate(lines, 1):
            match = self.SESSION_ID_PATTERN.search(line)
            if match:
                # Try to identify agent role
                role = "Unknown"
                if "Agent A" in line or "DevOps" in line:
                    role = "Agent A (DevOps)"
                elif "Agent B" in line or "Architect" in line:
                    role = "Agent B (Architect)"
                elif "Risk" in line:
                    role = "Risk Analyst"
                elif "PM" in line:
                    role = "PM"

                session_ids.append(SessionIdRecord(
                    agent_role=role,
                    session_id=match.group(1),
                    line_number=i
                ))

        return session_ids

    def _extract_decision_gates(self, lines: List[str]) -> List[DecisionGateRecord]:
        """Extract Decision Gate records"""
        decision_gates = []

        for i, line in enumerate(lines, 1):
            match = self.DECISION_GATE_PATTERN.search(line)
            if match:
                decision_text = match.group(1).strip()

                # Check if confirmed
                confirmed = "APPROVE" in decision_text.upper()

                # Try to extract date
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', line)
                date = date_match.group(0) if date_match else None

                decision_gates.append(DecisionGateRecord(
                    decision=decision_text,
                    session_id=None,
                    confirmed=confirmed,
                    date=date,
                    line_number=i
                ))

        return decision_gates

    def _extract_commands(
        self,
        lines: List[str],
        content: str
    ) -> List[CommandRecord]:
        """Extract command execution records"""
        commands = []

        for i, line in enumerate(lines, 1):
            match = self.COMMAND_PATTERN.search(line)
            if match:
                command = match.group(1).strip()

                output_present = False
                result = None

                # Look for result in subsequent lines
                for j in range(i, min(i + 10, len(lines))):
                    next_line = lines[j]
                    if self.RESULT_PATTERN.search(next_line):
                        output_present = True
                        m = self.RESULT_PATTERN.search(next_line)
                        result = m.group(1).strip() if m else ""
                        break
                    # Stop if another command is seen
                    if self.COMMAND_PATTERN.search(next_line):
                        break

                commands.append(CommandRecord(
                    command=command,
                    output_present=output_present,
                    result=result,
                    line_number=i
                ))

        return commands

    def _check_empty_records(self, content: str, errors: List[str]):
        """Check for empty/vague records"""
        # Check common empty record patterns
        empty_patterns = [
            r'✅\s*$',     # emoji only
            r'(PASS|passed|passed)\s*$',  # passed
        ]

        for pattern in empty_patterns:
            if re.search(pattern, content, re.MULTILINE):
                pass  # warning only, not an error


def run_check(
    project_path: Optional[str] = None,
    verbose: bool = True
) -> DevLogCheckResult:
    """
    Run DEVELOPMENT_LOG check.

    Args:
        project_path: project root directory path
        verbose: whether to print detailed output

    Returns:
        DevLogCheckResult: check result
    """
    if project_path is None:
        project_path = str(Path(__file__).parent.parent)

    checker = DevLogChecker(project_path)
    result = checker.check()

    if verbose:
        print(result.print_summary())

    return result


def main():
    """Command line entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Development Log Checker"
    )
    parser.add_argument(
        "project_path",
        nargs="?",
        default=None,
        help="Project root path"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Quiet mode (no verbose output)"
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--detail", "-d",
        action="store_true",
        help="Show detailed information"
    )

    args = parser.parse_args()

    result = run_check(
        project_path=args.project_path,
        verbose=not args.quiet
    )

    if args.json:
        print(result.to_json())

    if args.detail:
        print("\nDetailed Session IDs:")
        print("-" * 40)
        for sid in result.session_ids:
            print(f"  {sid.agent_role}: {sid.session_id} (line {sid.line_number})")

        print("\nDetailed Commands:")
        print("-" * 40)
        for cmd in result.commands:
            status = "OK" if cmd.output_present else "MISSING"
            print(f"  [{status}] {cmd.command}")
            if cmd.result:
                print(f"     Result: {cmd.result[:50]}...")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()

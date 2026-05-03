#!/usr/bin/env python3
"""
Spec Logic Checker - Automated Logic Correctness Checker
=========================================================
Automatically checks code for logic correctness, covering Quality Gate blind spots.

Features:
1. Check if output exceeds input (string operations)
2. Check branch consistency (single vs multiple)
3. Check for non-lazy external dependency init
4. Semantic validation (against SRS)

Usage:
    python scripts/spec_logic_checker.py /path/to/project
    python scripts/spec_logic_checker.py /path/to/project --srs /path/to/SRS.md
"""

import os
import re
import sys
import argparse
from pathlib import Path
from typing import Any, List, Dict, Tuple
from dataclasses import dataclass, field


@dataclass
class LogicIssue:
    """Logic issue"""
    file_path: str
    function_name: str
    line_number: int
    issue_type: str
    description: str
    severity: str = "HIGH"  # HIGH, MEDIUM, LOW


@dataclass
class SpecLogicCheckResult:
    """Check result"""
    passed: bool
    score: float
    issues: List[LogicIssue] = field(default_factory=list)
    files_checked: int = 0
    functions_checked: int = 0


class SpecLogicChecker:
    """Logic correctness checker"""

    # Detection patterns
    PATTERNS = {
        "string_insertion": [
            (r'\+\s*["\'][.?!\s]', "Possible extra character insertion (punctuation/space)"),
            (r'\+\s*"\.join\(', "Possible redundant character insertion"),
        ],
        "branch_inconsistency": [
            (r'if\s+len\([^)]+\)\s*==\s*1\s*:', "Single-case special handling; confirm consistency with multi-case"),
            (r'if\s+len\([^)]+\)\s*==\s*0\s*:', "Empty-case special handling"),
        ],
        "non_lazy_init": [
            (r'def\s+__init__.*ffmpeg\.', "__init__ directly calls ffmpeg, apply lazy check"),
            (r'def\s+__init__.*requests\.', "__init__ directly calls requests, apply lazy check"),
            (r'def\s+__init__.*import\s+ffmpeg', "__init__ directly imports ffmpeg, apply lazy check"),
        ],
    }

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.issues: List[LogicIssue] = []

    def scan_python_files(self) -> SpecLogicCheckResult:
        """Scan all Python files"""
        python_files = list(self.project_path.rglob("*.py"))

        # Exclude tests and virtual environments
        python_files = [
            f for f in python_files
            if "test" not in f.name.lower()
            and "venv" not in str(f)
            and "__pycache__" not in str(f)
        ]

        files_checked = 0
        functions_checked = 0

        for py_file in python_files:
            files_checked += 1
            try:
                content = py_file.read_text(encoding="utf-8")
                file_issues = self._check_file(content, str(py_file))
                self.issues.extend(file_issues)
                functions_checked += len(re.findall(r'def\s+\w+', content))
            except Exception:
                pass

        score = self._calculate_score(len(self.issues), functions_checked)

        return SpecLogicCheckResult(
            passed=score >= 80,
            score=score,
            issues=self.issues,
            files_checked=files_checked,
            functions_checked=functions_checked
        )

    def _check_file(self, content: str, file_path: str) -> List[LogicIssue]:
        """Check a single file"""
        issues = []
        lines = content.split('\n')

        for i, line in enumerate(lines, 1):
            if line.strip().startswith('#'):
                continue

            for pattern, desc in self.PATTERNS["string_insertion"]:
                if re.search(pattern, line):
                    issues.append(LogicIssue(
                        file_path=file_path,
                        function_name=self._get_function_name(lines, i),
                        line_number=i,
                        issue_type="string_insertion",
                        description=desc,
                        severity="HIGH"
                    ))

            for pattern, desc in self.PATTERNS["branch_inconsistency"]:
                if re.search(pattern, line):
                    issues.append(LogicIssue(
                        file_path=file_path,
                        function_name=self._get_function_name(lines, i),
                        line_number=i,
                        issue_type="branch_inconsistency",
                        description=desc,
                        severity="MEDIUM"
                    ))

            for pattern, desc in self.PATTERNS["non_lazy_init"]:
                if re.search(pattern, line, re.IGNORECASE):
                    issues.append(LogicIssue(
                        file_path=file_path,
                        function_name=self._get_function_name(lines, i),
                        line_number=i,
                        issue_type="non_lazy_init",
                        description=desc,
                        severity="HIGH"
                    ))

        return issues

    def _get_function_name(self, lines: List[str], current_line: int) -> str:
        for i in range(current_line - 1, -1, -1):
            match = re.match(r'def\s+(\w+)', lines[i])
            if match:
                return match.group(1)
        return "unknown"

    def _calculate_score(self, issue_count: int, function_count: int) -> float:
        if function_count == 0:
            return 100

        high_issues = sum(1 for i in self.issues if i.severity == "HIGH")
        medium_issues = sum(1 for i in self.issues if i.severity == "MEDIUM")

        deduction = high_issues * 10 + medium_issues * 5
        score = max(0, 100 - deduction)

        return score

    def print_report(self, result: SpecLogicCheckResult):
        print("\n" + "="*60)
        print("Spec Logic Checker Report")
        print("="*60)

        print("\nStatistics")
        print(f"   Files:     {result.files_checked}")
        print(f"   Functions: {result.functions_checked}")
        print(f"   Issues:    {len(result.issues)}")
        print(f"   Score:     {result.score}/100")

        print(f"\nResult: {'PASS' if result.passed else 'FAIL'}")

        if result.issues:
            print("\nIssue List")
            for issue in result.issues:
                print(f"\n   [{issue.severity}] {issue.file_path}:{issue.line_number}")
                print(f"   Function: {issue.function_name}")
                print(f"   Type:     {issue.issue_type}")
                print(f"   Desc:     {issue.description}")

        print("\n" + "="*60)


class SemanticValidator:
    """Semantic Validator - validates logic correctness against SRS"""

    def __init__(self, srs_path: str):
        self.srs_path = srs_path
        self.requirements = self._parse_srs()

    def _parse_srs(self) -> Dict[str, Any]:
        requirements: Dict[str, Any] = {}

        try:
            content = Path(self.srs_path).read_text(encoding="utf-8")

            for match in re.finditer(r'\|\s*FR-(\d+)\s*\|([^\n|]+)', content):
                fr_id = f"FR-{match.group(1)}"
                description = match.group(2).strip()

                verification = self._infer_verification(description)
                requirements[fr_id] = {
                    "description": description,
                    "verification": verification
                }
        except Exception as e:
            print(f"Warning: Failed to parse SRS: {e}")

        return requirements

    def _infer_verification(self, description: str) -> str:
        desc = description.lower()

        # Note: conditions match Chinese SRS content intentionally
        if "segment" in desc and ("char" in desc or "length" in desc):
            return "output_len_le_input"
        elif "merge" in desc:
            return "single_file_format_equals_multi"
        elif "retain" in desc or "punct" in desc:
            return "no_extra_char_insertion"
        elif "retry" in desc:
            return "L1_L2_retryable_L3_L4_not"
        elif "break" in desc or "circuit" in desc:
            return "consecutive_failures_trigger_circuit_break"
        elif "timeout" in desc:
            return "timeout_raises_TimeoutError"
        else:
            return "manual_verification_required"

    def verify(self, code: str, fr_id: str) -> Tuple[bool, str]:
        if fr_id not in self.requirements:
            return True, f"Requirement {fr_id} not found"

        requirement = self.requirements[fr_id]
        verification = requirement["verification"]

        if verification == "output_len_le_input":
            if re.search(r'\+\s*["\'][.?!\s]', code):
                return False, f"{fr_id} may insert extra characters"

        elif verification == "single_file_format_equals_multi":
            if re.search(r'if\s+len\([^)]+\)\s*==\s*1\s*:', code):
                return True, f"{fr_id} has special handling, confirm consistency"

        return True, "Logic conforms to SRS"


def main():
    parser = argparse.ArgumentParser(description="Spec Logic Checker")
    parser.add_argument("project_path", help="Project path")
    parser.add_argument("--srs", help="SRS.md path for semantic validation")
    args = parser.parse_args()

    if not os.path.exists(args.project_path):
        print(f"Error: path not found: {args.project_path}")
        sys.exit(1)

    checker = SpecLogicChecker(args.project_path)
    result = checker.scan_python_files()
    checker.print_report(result)

    # Semantic validation (optional)
    if args.srs and os.path.exists(args.srs):
        print("\n" + "="*60)
        print("Semantic Validation Report")
        print("="*60)
        validator = SemanticValidator(args.srs)
        print("\nSRS Requirements")
        print(f"   Total: {len(validator.requirements)}")

        for fr_id, req in list(validator.requirements.items())[:5]:
            print(f"\n   {fr_id}: {req['description'][:40]}...")
            print(f"   Verification: {req['verification']}")

        if len(validator.requirements) > 5:
            print(f"\n   ... {len(validator.requirements) - 5} more requirements")

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()

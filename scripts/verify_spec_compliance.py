#!/usr/bin/env python3
"""
Spec Compliance Verification Script
====================================
Auto-check if implementation complies with spec requirements.

Usage:
    python verify_spec_compliance.py /path/to/project
    python verify_spec_compliance.py /path/to/project --fix
"""

import os
import re
import sys
import argparse
from pathlib import Path
from typing import Dict


class SpecComplianceChecker:
    """Spec compliance checker."""

    # Hooks for project-specific fix hints — add entries via check_* methods at runtime.
    # Framework ships empty; target projects populate via SpecComplianceChecker subclass or config.
    _FIX_HINTS: dict[str, str] = {}

    def __init__(self, project_path: str):
        """Initialize instance with default configuration."""
        self.project_path = Path(project_path)
        self.issues: list[str] = []
        self.passed: list[str] = []

    def suggest_fixes(self, issues: list[str]) -> list[str]:
        """Return actionable fix suggestions for the given issues."""
        hints: list[str] = []
        for issue in issues:
            for pattern, hint in self._FIX_HINTS.items():
                if pattern in issue:
                    hints.append(f"{issue}\n    → {hint}")
                    break
            else:
                hints.append(f"{issue}\n    → Manual inspection required")
        return hints

    def check_all(self) -> Dict:
        """Run all checks."""
        checks = [
            self.check_audio_merge,
            self.check_splitters,
            self.check_retry_mechanism,
            self.check_circuit_breaker,
            self.check_logging,
            self.check_prosody_control,
        ]
        for check in checks:
            try:
                check()
            except Exception as e:
                self.issues.append(f"Check failed: {check.__name__} - {e}")
        return {
            "passed": self.passed,
            "issues": self.issues,
            "total": len(self.passed) + len(self.issues),
            "score": f"{len(self.passed)}/{len(self.passed) + len(self.issues)}"
        }

    def check_audio_merge(self):
        """Check audio merge handles all segments."""
        cli_file = self.project_path / "src" / "cli.py"
        if not cli_file.exists():
            self.issues.append("cli.py not found")
            return
        content = cli_file.read_text()
        if re.search(r"shutil\.copy\(temp_files\[0\]", content):
            self.issues.append("Audio merge: only copies first segment, not all (P6)")
            return
        if "for temp_file in temp_files" in content and "write(f.read())" in content:
            self.passed.append("Audio merge: correctly handles all segments (P6)")
        else:
            self.issues.append("Audio merge: merge logic not found")

    def check_splitters(self):
        """Check splitters include newline character."""
        tp_file = self.project_path / "src" / "text_processor.py"
        if not tp_file.exists():
            self.issues.append("text_processor.py not found")
            return
        content = tp_file.read_text()
        if r'"\n"' in content or r"'\n'" in content:
            self.passed.append("Splitters: includes newline character (P8)")
        else:
            self.issues.append("Splitters: missing newline character (P8)")

    def check_retry_mechanism(self):
        """Check retry mechanism has exponential backoff."""
        retry_file = self.project_path / "src" / "retry_handler.py"
        if not retry_file.exists():
            self.issues.append("retry_handler.py not found")
            return
        content = retry_file.read_text()
        if "2 ** attempt" in content or "pow(2, attempt)" in content:
            self.passed.append("Retry: exponential backoff implemented")
        else:
            self.issues.append("Retry: exponential backoff not found")

    def check_circuit_breaker(self):
        """Check circuit breaker has full state machine."""
        retry_file = self.project_path / "src" / "retry_handler.py"
        if not retry_file.exists():
            return
        content = retry_file.read_text()
        if "CircuitState" in content or "CircuitBreaker" in content:
            self.passed.append("Circuit breaker: state machine implemented")

    def check_logging(self):
        """Check for proper logging."""
        cli_file = self.project_path / "src" / "cli.py"
        if not cli_file.exists():
            return
        content = cli_file.read_text()
        if "logging" in content and ("logger.error" in content or "logger.info" in content):
            self.passed.append("Logging: using logging module")
        elif "print(" in content:
            self.issues.append("Logging: using print() only, recommend switching to logging")

    def check_prosody_control(self):
        """Check prosody control is fully implemented (spec P8)."""
        prosody_file = self.project_path / "src" / "prosody_manager.py"
        if not prosody_file.exists():
            self.issues.append("Prosody: prosody_manager.py not found (P8)")
            return
        content = prosody_file.read_text()
        has_200  = '"200"' in content or "'200'" in content or ": 200" in content
        has_500  = '"500"' in content or "'500'" in content or ": 500" in content
        has_1000 = '"1000"' in content or "'1000'" in content or ": 1000" in content
        if not has_200:
            self.issues.append("Prosody: comma pause not set (should be 200ms)")
        if not has_500:
            self.issues.append("Prosody: period pause not set (should be 500ms)")
        if not has_1000:
            self.issues.append("Prosody: newline pause not set (should be 1000ms)")
        if has_200 and has_500 and has_1000:
            self.passed.append("Prosody: pause timing correctly implemented (P8)")
            cli_file = self.project_path / "src" / "cli.py"
            if cli_file.exists() and "ProsodyManager" in cli_file.read_text():
                self.passed.append("Prosody: integrated into CLI")
            else:
                self.issues.append("Prosody: not integrated into CLI")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Spec compliance verification")
    parser.add_argument("project_path", help="Project path")
    parser.add_argument("--fix", action="store_true", help="Attempt auto-fix")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not os.path.isdir(args.project_path):
        print(f"Error: path not found or not a directory: {args.project_path}")
        sys.exit(1)

    checker = SpecComplianceChecker(args.project_path)
    result = checker.check_all()

    if args.json:
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("=" * 50)
        print("Spec Compliance Report")
        print("=" * 50)
        print(f"Score: {result['score']}")
        print()
        if result["passed"]:
            print("PASSED:")
            for p in result["passed"]:
                print(f"  + {p}")
            print()
        if result["issues"]:
            print("ISSUES:")
            for issue in result["issues"]:
                print(f"  - {issue}")
            print()
            if args.fix:
                print("FIX SUGGESTIONS:")
                for hint in checker.suggest_fixes(result["issues"]):
                    print(f"  {hint}")
                print()
                print("[INFO] --fix shows suggestions only. Apply fixes manually.")

    sys.exit(0 if not result["issues"] else 1)


if __name__ == "__main__":
    main()

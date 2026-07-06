#!/usr/bin/env python3
"""
Spec Compliance Verification Script
====================================
FR-driven: every FR module mapped in the project's SAD.md must exist on
disk and carry its [FR-XX] traceability marker. All check targets derive
from the project's own architecture document — this script must never
hardcode a particular target project's modules. (Its previous incarnation
shipped a past target project's module names and false-positive failed
every other project; E2E round 2 HIGH finding. tests/test_no_hardcoded_paths.py
now lint-blocks that class.)

Usage:
    python verify_spec_compliance.py /path/to/project
    python verify_spec_compliance.py /path/to/project --fix
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root for direct runs

from core.utils.project_layout import ProjectLayout  # noqa: E402
from detection.drift_detector import DriftDetector  # noqa: E402


class SpecComplianceChecker:
    """FR-implementation compliance checker (SAD.md is the source of truth)."""

    # Hooks for project-specific fix hints — add entries via config at runtime.
    # Framework ships empty; target projects populate via subclass or config.
    _FIX_HINTS: dict[str, str] = {}

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.layout = ProjectLayout(self.project_path)
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

    def _resolve_module(self, rel_path: str) -> Path | None:
        """Locate a SAD-mapped module on disk. SAD only commits to a
        basename-ish path, so mirror DriftDetector.detect_sad_drift's
        resolution: direct candidates first, then a recursive basename
        search under the active src dir (src-layout support)."""
        candidates = [
            self.project_path / rel_path,
            self.layout.phase3_development_dir / rel_path,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        basename = rel_path.split("/")[-1]
        src_dir = self.layout.active_src_dir
        if src_dir.is_dir():
            for found in sorted(src_dir.rglob(basename)):
                if found.is_file():
                    return found
        return None

    def check_all(self) -> Dict:
        """Check every FR→module mapping declared in SAD.md."""
        sad_path = self.layout.sad_path
        if not sad_path.is_file():
            # Fail closed: no SAD.md means no FR map — not a vacuous pass.
            self.issues.append(
                "SAD.md not found (02-architecture/ or project root) — "
                "cannot derive the FR→module map"
            )
            return self._result()

        content = sad_path.read_text(encoding="utf-8", errors="replace")
        mappings = DriftDetector.SAD_FR_PATTERN.findall(content)  # [(fr_num, path), ...]
        if not mappings:
            # Zero mappings is indistinguishable from a parse failure —
            # same fail-closed rule as preflight_traceability.
            self.issues.append(
                f"No FR→module mappings found in {sad_path.name} — "
                "nothing to verify (parse failure or missing FR table)"
            )
            return self._result()

        for fr_num, rel_path in mappings:
            fr_id = f"FR-{fr_num}"
            module = self._resolve_module(rel_path)
            if module is None:
                self.issues.append(f"{fr_id}: mapped module {rel_path} not found on disk")
                continue
            text = module.read_text(encoding="utf-8", errors="replace")
            if f"[{fr_id}]" in text:
                self.passed.append(f"{fr_id}: {rel_path} implemented with [{fr_id}] marker")
            else:
                self.issues.append(
                    f"{fr_id}: {self.layout.get_relative_str(module)} lacks the "
                    f"[{fr_id}] traceability marker"
                )
        return self._result()

    def _result(self) -> Dict:
        total = len(self.passed) + len(self.issues)
        return {
            "passed": self.passed,
            "issues": self.issues,
            "total": total,
            "score": f"{len(self.passed)}/{total}",
        }


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

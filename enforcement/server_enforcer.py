#!/usr/bin/env python3
"""
Server-Side Enforcer
====================
CI/CD-level enforcement that cannot be bypassed via --no-verify.

Purpose:
- Catch cases where local hooks were bypassed
- Ensure all pull requests pass enforcement
- Provide final server-side verification
"""

import os
import sys
from typing import Dict


class ServerEnforcer:
    """
    Server-Side Enforcer.

    Runs in CI/CD — cannot be bypassed::

        enforcer = ServerEnforcer()
        results = enforcer.enforce_all()
        if not results["all_passed"]:
            enforcer.report_failure(results)
            sys.exit(1)
    """

    def __init__(self):
        """Initialize instance."""
        self.checks = []
        self.results = {}
        self._setup_checks()

    def _setup_checks(self):
        self.checks.append({"name": "constitution", "fn": self._check_constitution, "required": True})
        self.checks.append({"name": "policy",       "fn": self._check_policy,       "required": True})
        self.checks.append({"name": "quality-gate", "fn": self._check_quality_gate, "required": True})
        self.checks.append({"name": "security",     "fn": self._check_security,     "required": True})

    def _check_constitution(self) -> Dict:
        try:
            from enforcement import ConstitutionAsCode
            c = ConstitutionAsCode()
            return {"passed": True, "rules": c.get_rules_summary()}
        except Exception as e:
            return {"passed": False, "error": str(e)}

    def _check_policy(self) -> Dict:
        try:
            from enforcement import PolicyEngine
            e = PolicyEngine()
            e.enforce_all()
            summary = e.get_summary()
            return {"passed": summary["all_passed"], "summary": summary}
        except Exception as e:  # pragma: no cover
            return {"passed": False, "error": str(e)}

    def _check_quality_gate(self) -> Dict:
        try:
            from ai_quality_gate import AIQualityGate
            gate = AIQualityGate()
            result = gate.scan_directory(".")
            return {"passed": result["score"] >= 90, "score": result["score"]}
        except Exception as e:  # pragma: no cover
            return {"passed": False, "error": str(e)}

    def _check_security(self) -> Dict:
        try:
            from security_scanner import SecurityScanner
            scanner = SecurityScanner()
            result = scanner.scan_directory(".")
            return {"passed": result["score"] >= 95, "score": result["score"]}
        except Exception as e:  # pragma: no cover
            return {"passed": False, "error": str(e)}

    def enforce_all(self) -> Dict:
        """Enforce all."""
        self.results = {}
        for check in self.checks:
            name = check["name"]
            try:
                result = check["fn"]()
                self.results[name] = result
            except Exception as e:
                self.results[name] = {"passed": False, "error": str(e)}
        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r.get("passed", False))
        return {
            "all_passed": passed == total,
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "results": self.results,
        }

    def report_failure(self, results: Dict):
        """Report failure."""
        print("=" * 60)
        print("SERVER-SIDE ENFORCEMENT FAILED")
        print("=" * 60)
        for name, result in results["results"].items():
            if not result.get("passed", False):
                print(f"  FAIL: {name}")
                if "error" in result:
                    print(f"    Error: {result['error']}")
                if "score" in result:
                    print(f"    Score: {result['score']}")
        print(f"\nFailed: {results['failed']}/{results['total']}")
        print("This check cannot be bypassed with --no-verify")

    def on_git_hook(self, hook_type: str = "pre-commit") -> bool:
        """Trigger enforcement from a git hook."""
        from enforcement.framework_enforcer import FrameworkEnforcer
        print(f"Running Framework Enforcement for {hook_type}...")
        enforcer = FrameworkEnforcer(os.getcwd())
        result = enforcer.run(level="BLOCK")
        if not result.passed:
            print("\nEnforcement Failed:")
            for msg, fix in result.violations:
                print(f"  FAIL: {msg}")
                if fix:
                    print(f"    Run: {fix}")
            return False
        print("Framework Enforcement passed")
        return True


def main():
    """CLI entry point."""
    enforcer = ServerEnforcer()
    results = enforcer.enforce_all()
    if not results["all_passed"]:
        enforcer.report_failure(results)
        sys.exit(1)
    else:
        print("=" * 60)
        print("ALL SERVER-SIDE ENFORCEMENT CHECKS PASSED")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()

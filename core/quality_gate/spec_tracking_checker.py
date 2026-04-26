#!/usr/bin/env python3
"""
Specification Tracking Checker
Check whether SPEC_TRACKING.md exists and is complete

Usage:
    from quality_gate.spec_tracking_checker import SpecTrackingChecker
    checker = SpecTrackingChecker("/path/to/project")
    result = checker.run()
"""

import os
import re
from typing import Dict, List, Optional
from pathlib import Path


class SpecTrackingChecker:
    """Specification tracking completeness checker"""
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        # Support multiple possible locations
        self.spec_file_candidates = [
            self.project_root / "SPEC_TRACKING.md",
            self.project_root / "01-requirements" / "SPEC_TRACKING.md",
            self.project_root / "01-specify" / "SPEC_TRACKING.md",
            self.project_root / "requirements" / "SPEC_TRACKING.md",
        ]
        self.template_file = Path(__file__).parent.parent / "templates" / "SPEC_TRACKING.md"
        self.spec_file = None
        for candidate in self.spec_file_candidates:
            if candidate.exists():
                self.spec_file = candidate
                break
        if self.spec_file is None:
            self.spec_file = self.spec_file_candidates[0]  # default to first
    
    def check_exists(self) -> bool:
        """Check whether SPEC_TRACKING.md exists"""
        return any(c.exists() for c in self.spec_file_candidates)
    
    def check_completeness(self) -> Dict:
        """Check specification tracking completeness"""
        if not self.check_exists():
            return {
                "complete": False,
                "missing": ["SPEC_TRACKING.md not found"],
                "errors": []
            }
        
        content = self.spec_file.read_text(encoding="utf-8")
        missing = []
        errors = []
        
        # Check if core features table exists
        if not self._has_table(content, "Core Features"):
            missing.append("Core Features table")
        
        # Check if status column exists
        if "Status" not in content:
            missing.append("Status column")
        
        # Check if update log exists
        if not self._has_update_log(content):
            missing.append("Update log")
        
        # Check all entries have status
        entries_without_status = self._find_entries_without_status(content)
        if entries_without_status:
            for entry in entries_without_status:
                missing.append(f"Entry missing status: {entry}")
        
        return {
            "complete": len(missing) == 0,
            "missing": missing,
            "errors": errors
        }
    
    def _has_table(self, content: str, table_name: str) -> bool:
        """Check if specified table exists"""
        pattern = rf"{table_name}.*\|.*\|"
        return bool(re.search(pattern, content, re.DOTALL))
    
    def _has_update_log(self, content: str) -> bool:
        """Check if update log exists"""
        return ("Update log" in content) and "Date" in content and "|" in content
    
    def _find_entries_without_status(self, content: str) -> List[str]:
        """Find entries without status"""
        entries = []
        # Find all table rows
        lines = content.split("\n")
        for line in lines:
            if "|" in line and not line.strip().startswith("|"):
                # Check if data row (not header or separator)
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4 and not any(x in parts[1] for x in ["Spec", "Requirement", "Item"]):
                    # Check if last non-empty column is status
                    status_col = None
                    for p in reversed(parts):
                        if p:
                            status_col = p
                            break
                    if status_col and not any(x in status_col for x in ["✅", "⚠️", "❌", "Done", "Pending", "Not Implemented"]):
                        entries.append(parts[1] if len(parts) > 1 else "Unknown")
        return entries
    
    def run(self) -> bool:
        """Run specification tracking check (backward-compatible, returns bool)"""
        if not self.check_exists():
            return False
        return self.check_completeness()["complete"]
    
    def run_enforcement(self) -> Dict:
        """
        Run specification tracking check (for Enforcement integration)
        
        Returns:
            Dict with keys:
                - exists: bool
                - completeness: int (0-100)
                - complete: bool
                - missing: List[str]
                - errors: List[str]
        """
        exists = self.check_exists()
        if not exists:
            return {
                "exists": False,
                "completeness": 0,
                "complete": False,
                "missing": ["SPEC_TRACKING.md not found"],
                "errors": []
            }
        
        completeness_result = self.check_completeness()
        content = self.spec_file.read_text(encoding="utf-8")
        stats = self._count_status(content)
        
        total = sum(stats.values())
        completed = stats.get("✅ Done", 0)
        
        # Calculate completeness percentage
        completeness_pct = int((completed / max(total, 1)) * 100) if total > 0 else 0
        
        return {
            "exists": True,
            "completeness": completeness_pct,
            "complete": completeness_result["complete"],
            "missing": completeness_result["missing"],
            "errors": completeness_result["errors"],
            "stats": stats
        }
    
    def print_report(self):
        """Print specification tracking report"""
        if not self.check_exists():
            print("❌ SPEC_TRACKING.md not found")
            print("   Run 'python3 cli.py spec-track init' to initialize")
            return
        
        completeness = self.check_completeness()
        
        print("=" * 50)
        print("Specification Tracking Report")
        print("=" * 50)
        
        if completeness["complete"]:
            print("✅ Specification tracking complete")
        else:
            print("❌ Specification tracking incomplete")
        
        if completeness["missing"]:
            print("\nMissing items:")
            for item in completeness["missing"]:
                print(f"  • {item}")
        
        # Read and display status statistics
        content = self.spec_file.read_text(encoding="utf-8")
        stats = self._count_status(content)
        if stats:
            print("\nStatus statistics:")
            for status, count in stats.items():
                print(f"  {status}: {count}")
    
    def _count_status(self, content: str) -> Dict[str, int]:
        """Count entries per status"""
        stats = {
            "✅ Done": 0,
            "⚠️ Pending": 0,
            "❌ Not Implemented": 0
        }
        
        for line in content.split("\n"):
            if "✅" in line:
                stats["✅ Done"] += 1
            elif "⚠️" in line:
                stats["⚠️ Pending"] += 1
            elif "❌" in line:
                stats["❌ Not Implemented"] += 1
        
        return stats


def main():
    """Command-line entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Specification Tracking Checker")
    parser.add_argument("project_root", nargs="?", default=".",
                       help="project root directory (default: current directory)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    
    args = parser.parse_args()
    
    checker = SpecTrackingChecker(args.project_root)
    
    if not args.json:
        result = checker.run()
        checker.print_report()
        return 0 if result else 1
    else:
        completeness = checker.check_completeness()
        print(completeness)
        return 0 if completeness["complete"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())

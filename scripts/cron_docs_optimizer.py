#!/usr/bin/env python3
"""
Docs Optimizer - Automated documentation optimization script

Features:
1. Check version consistency (README badge vs cli.py VERSION)
2. Update README command count
3. Sync case index (docs/cases/README.md)
4. Check TODO/FIXME comments
5. Check file integrity

Trigger:
- Cron job: Runs hourly
- Manual: python cron_docs_optimizer.py --check
- Fix:    python cron_docs_optimizer.py --fix
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime

# Settings
WORKSPACE = Path(__file__).parent.parent
README = WORKSPACE / "README.md"
CLI_PY = WORKSPACE / "cli.py"
CASES_README = WORKSPACE / "docs" / "cases" / "README.md"
DOCS_DIR = WORKSPACE / "docs"


class DocsOptimizer:
    def __init__(self, dry_run=True):
        self.dry_run = dry_run
        self.fixes = []
        self.warnings = []

    def run(self, fix=False):
        """Run document check/fix"""
        self.dry_run = not fix

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Docs Optimizer {'FIX' if fix else 'CHECK'}")
        print("=" * 50)

        # 1. Version consistency check
        self.check_version_consistency()

        # 2. Update README command count
        self.check_command_count()

        # 3. Sync case index
        self.sync_case_index()

        # 4. Check TODO/FIXME
        self.check_todos()

        # 5. Check file integrity
        self.check_file_integrity()

        # Summary
        self.print_summary()

        return len(self.fixes) == 0

    def check_version_consistency(self):
        """Check version consistency"""
        print("\n[1/5] Version consistency check...")

        badge_pattern = r'version-(v[\d.]+)-'
        with open(README, 'r', encoding='utf-8') as f:
            readme_content = f.read()

        badge_match = re.search(badge_pattern, readme_content)
        readme_version = badge_match.group(1) if badge_match else None

        version_pattern = r'VERSION\s*=\s*["\']([^"\']+)["\']'
        with open(CLI_PY, 'r', encoding='utf-8') as f:
            cli_content = f.read()

        cli_match = re.search(version_pattern, cli_content)
        cli_version = cli_match.group(1) if cli_match else None

        # Compare (normalize: ensure v prefix on both)
        if readme_version and cli_version:
            readme_normalized = readme_version if readme_version.startswith('v') else f'v{readme_version}'
            cli_normalized = cli_version if cli_version.startswith('v') else f'v{cli_version}'

            if readme_normalized == cli_normalized:
                print(f"   OK Version consistent: {readme_normalized}")
            else:
                self.warnings.append(f"Version inconsistent: README={readme_version}, cli.py={cli_version}")
                print(f"   WARN Version inconsistent: README={readme_version}, cli.py={cli_version}")
                if not self.dry_run:
                    self.fix_version_consistency(readme_version, cli_version)
        else:
            self.warnings.append("Unable to read version info")
            print("   ERR Unable to read version info")

    def fix_version_consistency(self, readme_version, cli_version):
        """Fix version consistency"""
        with open(CLI_PY, 'r', encoding='utf-8') as f:
            content = f.read()

        new_content = re.sub(
            r'VERSION\s*=\s*["\'][^"\']+["\']',
            f'VERSION = "{readme_version}"',
            content
        )

        with open(CLI_PY, 'w', encoding='utf-8') as f:
            f.write(new_content)

        self.fixes.append(f"Updated cli.py VERSION: {cli_version} -> {readme_version}")
        print(f"   FIXED: cli.py {cli_version} -> {readme_version}")

    def check_command_count(self):
        """Check README command count"""
        print("\n[2/5] Command count check...")

        cmd_pattern = r'def cmd_(\w+)\('
        with open(CLI_PY, 'r', encoding='utf-8') as f:
            content = f.read()

        commands = re.findall(cmd_pattern, content)
        cmd_count = len(commands)

        print(f"   CLI command count: {cmd_count}")

        with open(README, 'r', encoding='utf-8') as f:
            readme_content = f.read()

        readme_cmd_section = re.search(r'## CLI Commands.*?(?=##|\Z)', readme_content, re.DOTALL)
        if readme_cmd_section:
            readme_cmd_count = len(re.findall(r'\| `\w+` \|', readme_cmd_section.group(0)))
            if readme_cmd_count != cmd_count:
                self.warnings.append(f"Command count mismatch: README={readme_cmd_count}, actual={cmd_count}")
                print(f"   WARN README command count may be stale: README={readme_cmd_count}, actual={cmd_count}")

    def sync_case_index(self):
        """Sync case index"""
        print("\n[3/5] Case index sync...")

        cases_dir = DOCS_DIR / "cases"
        if not cases_dir.exists():
            self.warnings.append("docs/cases/ directory not found")
            print("   ERR docs/cases/ directory not found")
            return

        case_files = sorted(cases_dir.glob("case*.md"))
        case_count = len(case_files)

        print(f"   Case count: {case_count}")

        if CASES_README.exists():
            with open(CASES_README, 'r', encoding='utf-8') as f:
                cases_content = f.read()

            table_lines = re.findall(r'\| \d+ \|', cases_content)
            readme_case_count = len(table_lines)

            if readme_case_count != case_count:
                self.warnings.append(f"Case count mismatch: README={readme_case_count}, actual={case_count}")
                print(f"   WARN README cases: {readme_case_count}, actual: {case_count}")

    def check_todos(self):
        """Check TODO/FIXME"""
        print("\n[4/5] TODO/FIXME check...")

        todos = []
        fixmes = []

        for py_file in WORKSPACE.glob("*.py"):
            if py_file.name.startswith('cron_'):
                continue

            with open(py_file, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f, 1):
                    if 'TODO' in line:
                        todos.append(f"  {py_file.name}:{i} - {line.strip()[:60]}")
                    if 'FIXME' in line:
                        fixmes.append(f"  {py_file.name}:{i} - {line.strip()[:60]}")

        if todos:
            print(f"   TODO: {len(todos)} locations")
            for t in todos[:5]:
                print(t)
            if len(todos) > 5:
                print(f"   ... {len(todos) - 5} more")

        if fixmes:
            print(f"   FIXME: {len(fixmes)} locations")
            for fix in fixmes[:5]:
                print(fix)

    def check_file_integrity(self):
        """Check file integrity"""
        print("\n[5/5] File integrity check...")

        required_files = [
            "README.md",
            "cli.py",
            "SKILL.md",
            "docs/cases/README.md",
        ]

        for file_path in required_files:
            full_path = WORKSPACE / file_path
            if full_path.exists():
                print(f"   OK {file_path}")
            else:
                self.warnings.append(f"Missing file: {file_path}")
                print(f"   MISSING: {file_path}")

        core_modules = [
            "agent_spawner.py",
            "agent_team.py",
            "tool_registry.py",
            "hybrid_workflow.py",
        ]

        print("   Core modules:")
        for module in core_modules:
            if (WORKSPACE / module).exists():
                print(f"   OK {module}")
            else:
                self.warnings.append(f"Missing core module: {module}")
                print(f"   MISSING {module}")

    def print_summary(self):
        """Print summary"""
        print("\n" + "=" * 50)
        print("Summary")
        print("=" * 50)

        if self.dry_run:
            print("Mode: DRY RUN (check only, no fix)")
        else:
            print("Mode: FIX (applied)")

        if self.fixes:
            print(f"\nFixed ({len(self.fixes)} items):")
            for f in self.fixes:
                print(f"   * {f}")

        if self.warnings:
            print(f"\nWarnings ({len(self.warnings)} items):")
            for w in self.warnings:
                print(f"   * {w}")

        if not self.fixes and not self.warnings:
            print("\nAll checks passed")


def main():
    fix = "--fix" in sys.argv

    optimizer = DocsOptimizer()
    success = optimizer.run(fix=fix)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Version Bump Script
==================
Synchronizes version number across all project files.

Usage:
    python scripts/bump_version.py          # Show current version
    python scripts/bump_version.py 6.13.0  # Bump to specified version
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def get_current_version():
    """Read current version from __init__.py"""
    init_file = PROJECT_ROOT / "__init__.py"
    content = init_file.read_text()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


def _bump_readme_only_current(
    content: str,
    pattern: str,
    replacement: str,
    current_version_str: str = "",
) -> tuple[str, int]:
    """Bump README only on lines that look like a 'current version' marker.

    Lines that mention historical / legacy versions (e.g. inside a
    changelog or deprecation note) are preserved.

    `current_version_str` is accepted for future use (e.g. to detect
    that a legacy mention refers to an older version) and is currently
    a no-op parameter.
    """
    del current_version_str  # reserved for future use
    rx = re.compile(pattern)
    marker_re = re.compile(r"(?i)(current\s+version|version\s*[:=]|^#+\s*version)")
    new_lines: list[str] = []
    count = 0
    for line in content.splitlines(keepends=True):
        if rx.search(line) and marker_re.search(line):
            replaced = rx.sub(replacement, line)
            new_lines.append(replaced)
            count += len(rx.findall(line))
        else:
            new_lines.append(line)
    return "".join(new_lines), count


def bump_version(new_version: str):
    """Update version across all project files."""
    files_patterns = {
        "__init__.py": (r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new_version}"'),
        "pyproject.toml": (r'version\s*=\s*"[^"]+"', f'version = "{new_version}"'),
        "README.md": (r'v\d+\.\d+\.\d+', f"v{new_version}"),
    }

    updated = []
    for filename, (pattern, replacement) in files_patterns.items():
        filepath = PROJECT_ROOT / filename
        if not filepath.exists():
            print(f"  SKIP: {filename} not found")
            continue

        content = filepath.read_text()
        if filename == "README.md":
            # Bug M25 fix: previous pattern r'v\d+\.\d+\.\d+' matched
            # every v-version mention in README, including historical
            # references like "v1.0.0 (legacy)". Restrict to lines that
            # contain the current version marker (e.g. "Current version:
            # v2.0.0" or "Version: v2.0.0"), not legacy / changelog mentions.
            new_content, count = _bump_readme_only_current(content, pattern, replacement, current_version_str=replacement)
        else:
            new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            filepath.write_text(new_content)
            updated.append((filename, count))
            print(f"  OK: {filename}: updated {count} occurrence(s)")
        else:
            print(f"  SKIP: {filename}: pattern not found")

    print(f"\nVersion bumped to v{new_version}")
    return updated


def main():
    """CLI entry point."""
    if len(sys.argv) > 1:
        new_version = sys.argv[1]
        if not re.match(r'^\d+\.\d+\.\d+$', new_version):
            print(f"Invalid version format: {new_version}")
            print("Expected: MAJOR.MINOR.PATCH (e.g., 6.13.0)")
            sys.exit(1)
        bump_version(new_version)
    else:
        current = get_current_version()
        print(f"Current version: v{current}")


if __name__ == "__main__":
    main()

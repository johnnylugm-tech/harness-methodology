#!/usr/bin/env python3
"""
Verify tool availability for Harness Quality Framework.

Checks:
1. Core tools (always required — includes CRG)
2. Extended dimension tools (optional)

Usage:
  python3 scripts/verify_tools.py              # Check all
  python3 scripts/verify_tools.py --core       # Only core
  python3 scripts/verify_tools.py --extended   # Only extended
  python3 scripts/verify_tools.py --install-guide  # Print install commands
"""

import subprocess
import sys
import json

# Language-independent requirements (every project).
CORE_COMMON = {
    "git": ("git --version", "git 2.0+"),
    "code-review-graph": (
        "code-review-graph status",
        "pipx install code-review-graph",
        "Architecture analysis (required)",
    ),
    "gitleaks": ("gitleaks version", "brew install gitleaks", "Secrets scanning"),
}

# Per-language gate toolchain requirements (state.json `language`).
CORE_BY_LANG = {
    "python": {
        "python3": ("python3 --version", "Python 3.10+"),
        "pip3": ("pip3 --version", "pip 20+"),
        "ruff": ("ruff --version", "pip3 install ruff", "Linting"),
        "pyright": ("pyright --version", "pip3 install pyright", "Type safety"),
        "pytest": ("pytest --version", "pip3 install pytest", "Testing"),
        "coverage": ("coverage --version", "pip3 install coverage", "Coverage"),
        "bandit": ("bandit --version", "pip3 install bandit", "Security (SAST)"),
        "radon": ("radon --version", "pip3 install radon", "Maintainability index"),
    },
    "javascript": {
        "node": ("node --version", "Node.js 18+"),
        "npm": ("npm --version", "npm 9+"),
        "eslint": ("npx --no-install eslint --version",
                   "npm i -D (templates/js_toolchain/package.json)",
                   "Linting"),
        "tsc": ("npx --no-install tsc --version",
                "npm i -D (templates/js_toolchain/package.json)",
                "Type safety (JSDoc via --checkJs)"),
        "semgrep": ("semgrep --version", "pip3 install semgrep",
                    "Security (SAST, vendored ruleset)"),
    },
}
# TypeScript shares the JS toolchain; only the tsc role differs (native types).
CORE_BY_LANG["typescript"] = {
    **CORE_BY_LANG["javascript"],
    "tsc": ("npx --no-install tsc --version",
            "npm i -D (templates/js_toolchain/package.json)",
            "Type safety (tsc --noEmit)"),
}

# Test-runner requirement for JS/TS (state.json `test_runner`; default vitest).
RUNNER_TOOLS = {
    "vitest": ("npx --no-install vitest --version",
               "npm i -D (templates/js_toolchain/package.json)",
               "Testing + coverage"),
    "jest": ("npx --no-install jest --version",
             "npm i -D jest",
             "Testing + coverage"),
}


def core_tools_for(language: str, test_runner: str | None = None) -> dict:
    """Merged CORE tool table for *language* (common + language + runner)."""
    tools = {**CORE_COMMON, **CORE_BY_LANG.get(language, {})}
    if language in ("javascript", "typescript"):
        runner = test_runner or "vitest"
        if runner in RUNNER_TOOLS:
            tools[runner] = RUNNER_TOOLS[runner]
    return tools

EXTENDED_TOOLS = {
    # HIGH priority
    "mutmut": ("mutmut --version", "pip3 install mutmut", "Python mutation testing"),
    "stryker": (
        "stryker --version",
        "npm install -g @stryker-mutator/core",
        "JS mutation testing",
    ),
    # MEDIUM priority
    "hypothesis": (
        "python3 -c 'import hypothesis; print(hypothesis.__version__)'",
        "pip3 install hypothesis",
        "Property testing",
    ),
    "fast-check": (
        "npm list -g fast-check",
        "npm install -g fast-check",
        "JS property testing",
    ),
    "atheris": (
        "python3 -c 'import atheris'",
        "pip3 install atheris",
        "Python fuzzing",
    ),
    "pa11y": ("pa11y --version", "npm install -g pa11y", "Accessibility testing"),
    # LOW priority
    "scancode": (
        "scancode --version",
        "pip3 install scancode-toolkit",
        "License scanning",
    ),
    "syft": ("syft --version", "brew install syft", "SBOM generation"),
    "grype": ("grype --version", "brew install grype", "Vulnerability scanning"),
    "cosign": (
        "cosign version",
        "brew install sigstore/sigstore/cosign",
        "Code signing",
    ),
}

def check_command(cmd):
    """Return True if command exists and works.

    Probes run through `bash -c` so multi-word commands ("python3 --version",
    "npx --no-install eslint --version") work — same convention as
    harness_cli._run_tool_check.
    """
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_tools(tools_dict, category):
    """Check tool availability and return results."""
    results = {"category": category, "installed": 0, "missing": 0, "tools": {}}

    for tool, (check_cmd, *meta) in tools_dict.items():
        is_installed = check_command(check_cmd)
        install_cmd = meta[0] if len(meta) > 0 else None
        description = meta[1] if len(meta) > 1 else None

        results["tools"][tool] = {
            "installed": is_installed,
            "install_cmd": install_cmd,
            "description": description,
        }

        if is_installed:
            results["installed"] += 1
            status = "✓"
        else:
            results["missing"] += 1
            status = "✗"

        desc_str = f" ({description})" if description else ""
        print(f"  {status} {tool}{desc_str}")

    return results


def print_summary(results):
    """Print summary of tool verification."""
    print("\n" + "=" * 70)
    print("TOOL VERIFICATION SUMMARY")
    print("=" * 70)

    total_core = len(results["core"]["tools"]) if "core" in results else 0
    total_ext = len(EXTENDED_TOOLS)

    print(f"\n✓ Core Tools:     {results['core']['installed']}/{total_core}")
    if results["core"]["missing"] > 0:
        print(f"  Missing: {results['core']['missing']} (required)")

    print(f"\n✓ Extended Tools: {results['extended']['installed']}/{total_ext}")
    if results["extended"]["missing"] > 0:
        print(f"  Missing: {results['extended']['missing']} (optional)")

    # Recommendations
    print("\n" + "-" * 70)
    print("NEXT STEPS")
    print("-" * 70)

    if results["core"]["missing"] > 0:
        print(f"\n❌ BLOCKING: {results['core']['missing']} core tools missing")
        print("   Install missing tools before running framework")
        for tool, info in results["core"]["tools"].items():
            if not info["installed"] and info["install_cmd"]:
                print(f"   → {info['install_cmd']}")
    else:
        print("\n✅ All core tools available")

    if results["extended"]["missing"] > 0:
        print(
            f"\n⚠️  {results['extended']['missing']} extended tools missing (optional)"
        )
        print("   Run: ./scripts/install_extended_tools.sh --high")
    else:
        print("\n✅ All extended tools available")

    print()


def print_install_guide(category=None, language="python"):
    """Print installation commands organized by tool manager."""
    print("\nINSTALLATION GUIDE")

    tools_to_check = {}
    if category == "core":
        tools_to_check = core_tools_for(language)
    elif category == "extended":
        tools_to_check = EXTENDED_TOOLS
    else:
        tools_to_check = {**core_tools_for(language), **EXTENDED_TOOLS}

    print("\n" + "=" * 70)
    print("INSTALLATION GUIDE")
    print("=" * 70)

    # Group by priority for extended
    priorities = {
        "HIGH (test quality foundation)": ["mutmut", "stryker"],
        "MEDIUM (edge cases + fuzzing)": [
            "hypothesis",
            "fast-check",
            "atheris",
            "pa11y",
        ],
        "LOW (governance + observability)": ["scancode", "syft", "grype", "cosign"],
    }

    for priority, tool_list in priorities.items():
        print(f"\n{priority}")
        print("-" * 70)

        for tool in tool_list:
            if tool not in tools_to_check:
                continue

            info = tools_to_check[tool]
            install_cmd = info[1] if len(info) > 1 else None

            if install_cmd:
                print(f"  {tool}:")
                print(f"    {install_cmd}")


def _project_language(project_root, flag_value):
    """--language flag wins; else read .methodology/state.json; else python."""
    if flag_value:
        return flag_value
    state_path = f"{project_root}/.methodology/state.json"
    try:
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        return state.get("language") or "python"
    except (OSError, json.JSONDecodeError):
        return "python"


def _project_test_runner(project_root, flag_value):
    if flag_value:
        return flag_value
    state_path = f"{project_root}/.methodology/state.json"
    try:
        with open(state_path, encoding="utf-8") as fh:
            return json.load(fh).get("test_runner")
    except (OSError, json.JSONDecodeError):
        return None


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Verify tool availability")
    parser.add_argument("--core", action="store_true", help="Check only core tools")
    parser.add_argument(
        "--extended", action="store_true", help="Check only extended tools"
    )
    parser.add_argument(
        "--install-guide", action="store_true", help="Print installation commands"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--project", default=".",
                        help="Project root (reads language from state.json)")
    parser.add_argument("--language", default=None,
                        help="Override language (python/javascript/typescript)")
    parser.add_argument("--test-runner", default=None,
                        help="Override JS/TS test runner (vitest/jest)")

    args = parser.parse_args()
    language = _project_language(args.project, args.language)
    test_runner = _project_test_runner(args.project, args.test_runner)

    # Determine what to check
    check_all = not any([args.core, args.extended, args.install_guide])

    if args.install_guide:
        if args.core:
            print_install_guide("core", language)
        elif args.extended:
            print_install_guide("extended", language)
        else:
            print_install_guide(language=language)
        return 0

    results = {}

    if check_all or args.core:
        print("\n" + "=" * 70)
        print(f"CORE TOOLS (Required — includes CRG) [language: {language}]")
        print("=" * 70)
        results["core"] = check_tools(core_tools_for(language, test_runner), "core")

    if check_all or args.extended:
        print("\n" + "=" * 70)
        print("EXTENDED TOOLS (Optional)")
        print("=" * 70)
        results["extended"] = check_tools(EXTENDED_TOOLS, "extended")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if check_all:
            print_summary(results)

    # Exit with error if core tools missing
    if "core" in results and results["core"]["missing"] > 0:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
